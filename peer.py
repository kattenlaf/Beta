import hashlib
import os.path
import time
import types
import socket, socketserver
import threading
import selectors # https://realpython.com/python-sockets/
import urllib
import requests
import struct
import random
import logging
from collections import defaultdict
from collections import deque
from queue import Queue
from datetime import datetime
from ssl import socket_error
from _datetime import timedelta
import progressbar
# Personal files
import helpers
import torrent
from helpers import PSTRLEN_BYTES_LEN, PSTR_BYTES_LEN, RESERVED_BYTES_LEN, INFO_HASH_BYTES_LEN, PEER_ID_BYTES_LEN

download_bar = progressbar.ProgressBar(maxval=100)
download_bar.start()

# Some constants
BLOCK_BUFFER_SIZE = 16384 # 16KB per block size, 16384 BYTES
PIECE_BUFFER_SIZE = 262144 # 262KB per piece size, 256000 BYTES
TEST_FILE_BUFFER_SIZE = 256 # for testing purposes
MAX_PEERS_TO_SHARE_WITH = 50
TIMEOUT_FOR_PEER_DATA = 30
SOCKET_CONNECT_TIMEOUT = 10
MAX_RETRIES_TO_CONNECT = 2
RETRY_AFTER = 10
PEER_CONNECTION_RETRIES = 3 # maximum retries to connect to peers
DELAY_TO_CONNECT = 1 # delay to make initial socket connection to peer
NUM_OF_THREADS_FOR_DOWNLOAD = 15
# Choke related constants
CHECK_IFCHOKED_AFTER = 10
CHOKE_TIMEOUT = 30

#TESTING
TEST_TIMEOUT = 15

# Config key url parameters
PARAMETERS = 'url_parameters'
GET_TRACKER = 'get_tracker'

# Bitfield expected Length
BITFIELD_LENGTH = 8 # 8 bytes
MAXBACKLOG = 5

HOST = '0.0.0.0'
PORT = 23560
USABLE_PORTS = [PORT + _ for _ in range(PORT, PORT + NUM_OF_THREADS_FOR_DOWNLOAD + 20, 1)] # 20 extra ports for threads
SERVER_PORT = 23559
DELAY_FOR_FINDING_PEER = 30

# Error messages
PICKING_PEER_ERROR = "Error picking peer"

# semaphore locks for some resources
peer_connection_lock = threading.Lock() # lock related to connecting to a peer
downloading_lock = threading.Lock() # lock related to downloading

# prepare debugging and error files
error_log_file = 'error.log'
if os.path.exists(error_log_file):
    os.remove(error_log_file)
debug_log_file = 'debug.log'
if os.path.exists(debug_log_file):
    os.remove(debug_log_file)
info_log_file = 'info.log'
if os.path.exists(info_log_file):
    os.remove(info_log_file)

logging.basicConfig(
    filename='error.log',
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logging.basicConfig(
    filename = debug_log_file,
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

logging.basicConfig(
    filename = info_log_file,
    level=logging.BASIC_FORMAT,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Each peer/node is both a client and a server, should have way to send file and receive
# https://stackoverflow.com/questions/70962218/understanding-the-requisites-that-allow-bittorrent-peers-to-connect-to-each-othe
# https://docs.python.org/3/howto/sockets.html

class Bitfield:
    def __init__(self, received_bitfield=None):
        self.bitfield_array = bytearray()
        if received_bitfield is not None:
            self.bitfield_array = received_bitfield

    def has_piece(self, index):
        byte_index = index // 8
        offset = index % 8
        return self.bitfield_array[byte_index]>>(7-offset)&1 != 0

    def set_piece(self, index):
        byte_index = index // 8
        offset = index % 8
        self.bitfield_array[byte_index] |= 1 << (7-offset)

# All of the remaining messages in the protocol take the form of <length prefix><message ID><payload>.
# The length prefix is a four byte big-endian value. The message ID is a single decimal byte.
# The payload is message dependent.
# Each message has:
# Length - 32 bit integer, 4 bytes
# ID - denoting what type of message it is, 1 byte
# Payload - Remaining length of message

LENGTH_BYTES = 4
ID_BYTES = 1
class Message:
    def     __init__(self, recv_data=None, length=None, messageId=None, payload=None):
        if recv_data is not None:
            pos = 0
            self.length, pos = int.from_bytes(recv_data[pos:LENGTH_BYTES], "big"), pos + LENGTH_BYTES
            self.message_id, pos = helpers.MessageId(int.from_bytes(recv_data[pos:pos+ID_BYTES], "big")), pos + ID_BYTES
            self.payload = recv_data[pos:pos + self.length]
            self.message = self.length.to_bytes(4, "big") + int(self.message_id).to_bytes(1, "big") + self.payload
        else:
            # length here should probably be different, maybe misunderstood documentation?
            self.length = length
            self.message_id = messageId
            self.payload = payload
            self.message = self.length.to_bytes(4, "big") + int(self.message_id).to_bytes(1, "big") + self.payload

    def parse_piece_index_from_message(self):
        if self.message_id == helpers.MessageId.HAVE:
            return int(self.payload)

    def parse_piece_from_message(self):
        if self.message_id == helpers.MessageId.PIECE:
            length = self.length - helpers.MessageLength.PIECE
            block = Block(self.payload, length)
            return block

class Outside_Peer:
    def __init__(self, IP=None, port=None):
        self.IP = IP
        self.port = port
        self.bitfield = Bitfield()
        self.address = (IP, port)

    def __eq__(self, other):
        if self.IP == other.IP and self.port == other.port:
            return True
        return False

    def __hash__(self):
        return hash((self.IP, self.port))

    def set_bitfield(self, message: Message):
        if message is not None:
            bitfield_length = message.length - 0x0001
            self.bitfield.bitfield_array = message.payload

class Handshake:
    def __init__(self, info_hash=None, peer_id=None):
        self.pstr = b'BitTorrent protocol'
        self.pstrlen = len(self.pstr).to_bytes(1, byteorder='big')
        self.reserved = bytes(8) #\x00\x00...\x00
        self.info_hash = info_hash
        self.peer_id = peer_id


# <index><begin><block>
class Block:
    def __init__(self, payload, length):
        # first integer for index 4 bytes
        index = payload[0:4]
        self.index = int.from_bytes(index)
        # second integer for byte offset within the piece
        begin = payload[4:8]
        self.begin = int.from_bytes(begin)
        # rest is for the block of data
        self.data_block = payload[8:length+8]
        self.block_len = len(self.data_block)

class Piece:
    def __init__(self, piece_hash, index, torrent_piece_length, torrent_final_buf_length):
        self.index = index
        # Where does this piece begin and end in the final buffer of the whole file
        self.begin = index * torrent_piece_length
        end_ = self.begin + torrent_piece_length
        self.end = end_ if end_ < torrent_final_buf_length else torrent_final_buf_length
        self.piece_length = self.end - self.begin
        self.buffer = bytearray(self.piece_length)
        self.piece_hash = piece_hash
        self.downloaded = 0 # bytes downloaded
        self.requested = 0 # bytes requested
        self.backlog = 0 # how many blocks have been requested

    def place_block_in_buffer(self, block: Block):
        if block.begin >= len(self.buffer):
            raise ValueError("Begin offset is too high")
        block_end = block.begin + block.block_len
        if block_end > len(self.buffer):
            raise ValueError("block is outside buffer size")
        self.buffer[block.begin:block_end] = block.data_block
        self.downloaded += block.block_len
        self.backlog -= 1

    def is_downloading(self):
        if self.downloaded < self.piece_length:
            return True
        else:
            return False


class Peer:
    def __init__(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket_event_selector = selectors.DefaultSelector()
        self.times_peers_last_sent = {}
        self.torrent_details = torrent.Torrent()
        self.handshake_msg = Handshake(self.torrent_details.lookup_dict['info_hash'], self.torrent_details.peerID)
        self.check_peers_next = None
        self.other_peers_addresses = []
        self.current_peers_available_for_use_pos = 0
        self.number_pieces_to_download = len(self.torrent_details.info_pieces_list)
        self.pieces_to_download_queue = Queue(maxsize=self.number_pieces_to_download)
        self.peers_able_to_connect = deque()
        self.bitfield = Bitfield()
        self.finished_buffer = bytearray(self.torrent_details.info_length) # where we will write all the pieces we download to
        self.downloaded = 0
        self.init_pieces_to_download()

    # UPLOAD SKELETON -------------------
    def setup_server_sock(self):
        self.server_sock.bind((HOST, SERVER_PORT))
        self.server_sock.listen(MAX_PEERS_TO_SHARE_WITH)
        self.server_sock.setblocking(False)
        self.socket_event_selector.register(self.server_sock, selectors.EVENT_READ, data=None)

    def accept_connections(self, server_sock):
        connection_socket, addr = server_sock.accept()
        connection_socket.setblocking(False)
        data = types.SimpleNamespace(addr=addr, inb=b"", outb=b"")
        self.times_peers_last_sent[connection_socket.getpeername()] = datetime.now()
        events = selectors.EVENT_READ | selectors.EVENT_WRITE
        self.socket_event_selector.register(connection_socket, events, data=data)

    # bare bones implementation of how selector sockets will work
    def service_connection(self, key, mask):
        socket_connection = key.fileobj
        data = key.data
        remote_addr = socket_connection.getpeername()
        if mask & selectors.EVENT_READ:
            self.read_data(socket_connection, data)
        self.populate_data_to_send(data)
        if mask & selectors.EVENT_WRITE and remote_addr in self.times_peers_last_sent:
            if data.outb:
                bytes_sent = socket_connection.send(data.outb[:TEST_FILE_BUFFER_SIZE])
                socket_connection.send(b'\n')
                print(f'Sent to client\n{data.outb[:bytes_sent]}')
                data.outb = data.outb[bytes_sent:]

    # ------------

    def finished_downloading_file(self):
        return self.downloaded == self.torrent_details.info_length

    def check_piece_integrity(self, piece: Piece):
        download_piece_hash = bytes.fromhex(hashlib.sha1(piece.buffer).hexdigest())
        return download_piece_hash == piece.piece_hash

    def place_piece_in_buffer(self, piece: Piece):
        if not self.check_piece_integrity(piece):
            # Put piece back on queue if there's an issue doing checksum after downloading it
            piece = Piece(piece.piece_hash, piece.index, self.torrent_details.info_piece_length, self.torrent_details.info_length)
            self.pieces_to_download_queue.put(piece)
            return False
        begin = piece.index * self.torrent_details.info_piece_length
        end = begin + piece.piece_length
        self.finished_buffer[begin:end] = piece.buffer
        self.downloaded += piece.downloaded
        percentage_completed = (self.downloaded / self.torrent_details.info_length) * 100
        logging.info(f'Piece with index {piece.index} has finished downloading, progress -> {percentage_completed:.2f}%')
        download_bar.update(int(percentage_completed))
        # self.display_download_bar(percentage_completed, True, 50)
        return True

    def display_download_bar(self, percentage_download_completed, should_clear_line=False, num_bars=50):
        bars_complete = int((percentage_download_completed / 100.0) * num_bars)
        bar = '█' * bars_complete + '-' * (num_bars - bars_complete)
        if should_clear_line:
            print(f"download progress... |{bar}| {percentage_download_completed:.2f}%  ", end="\n")
        else:
            print(f"download progress... |{bar}| {percentage_download_completed:.2f}%  ", end="\n")

    # Place all the piece hashes to be downloaded in queue
    def init_pieces_to_download(self):
        for i in range(self.number_pieces_to_download):
            piece_hash = self.torrent_details.info_pieces_list[i]
            piece = Piece(piece_hash, i, self.torrent_details.info_piece_length, self.torrent_details.info_length)
            self.pieces_to_download_queue.put(piece)

    def retrieve_peers_from_tracker(self):
        if self.should_retrieve_peers():
            url_parameter_node = helpers.get_config_yaml().get(PARAMETERS)
            parameters = self.get_parameters_for_requests(url_parameter_node, GET_TRACKER)
            try:
                response = requests.get(self.torrent_details.announce, params=parameters)
                peers_from_response = self.torrent_details.get_peers_from_response(response)
                self.other_peers_addresses = self.unmarshal(peers_from_response)
                self.peers_able_to_connect = deque(self.other_peers_addresses)
                # self.set_connected_peers()
                self.check_peers_next = datetime.now() + timedelta(seconds=self.torrent_details.interval)
            except requests.exceptions.RequestException as exc:
                print(f'Exception making request to tracker: {exc}')
                # retry here potentially
            except Exception as e:
                print(f'Unhandled exception when making request to tracker: {e}')

    def get_parameters_for_requests(self, url_parameter_node, request_type):
        parameters = {}
        for param in url_parameter_node[request_type]:
            parameters[param] = self.torrent_details.lookup_dict[param]

        return parameters

    # Simple method to check if we should retrieve peers from the request again
    def should_retrieve_peers(self):
        if self.check_peers_next is None or self.check_peers_next < datetime.now():
            return True
        return False

    # Following method from here https://blog.jse.li/posts/torrent/
    # Unmarshal parses peer IP addresses and port
    def unmarshal(self, peers_from_response: bytes):
        peer_size = 6  # 4 for IP, 2 for port
        if len(peers_from_response) % peer_size != 0:
            raise ValueError("Received malformed peers")
        peers = []
        num_peers = len(peers_from_response) // peer_size
        for i in range(num_peers):
            offset = i * peer_size
            ip_bytes = peers_from_response[offset:offset + 4]
            port_bytes = peers_from_response[offset + 4:offset + 6]
            ip = socket.inet_ntoa(ip_bytes)
            # > for big endian, H for unsigned short int
            port = struct.unpack(">H", port_bytes)[0]
            peers.append(Outside_Peer(ip, port))
        return peers

    def receive_bitfield(self, sock: socket.socket):
        sock.settimeout(10)
        data = sock.recv(BLOCK_BUFFER_SIZE)
        message = Message(data)
        if message is None or message.message_id != helpers.MessageId.BITFIELD:
            return None
        else:
            return message # should be bitfield that the peer has

    # Key messages to send to peer for downloading / uploading piece
    def send_unchoke_message(self, sock: socket.socket):
        message = Message(recv_data=None, length=helpers.MessageLength.UNCHOKE, messageId=helpers.MessageId.UNCHOKE, payload=b'')
        sock.sendall(message.message)

    def send_interested_message(self, sock: socket.socket):
        message = Message(recv_data=None, length=helpers.MessageLength.INTERESTED, messageId=helpers.MessageId.INTERESTED, payload=b'')
        sock.sendall(message.message)

    def find_peer_to_connect_to(self, sock) -> Outside_Peer:
        def retry_delay(retries):
            retries += 1
            time.sleep(DELAY_TO_CONNECT)
            return retries

        still_connecting = True
        thread = threading.current_thread()
        while still_connecting:
            peer_connection_lock.acquire()
            if self.peers_able_to_connect:
                potential_peer = self.peers_able_to_connect.pop()
                peer_connection_lock.release()
            else:
                peer_connection_lock.release()
                break
            retries = 0
            while retries < PEER_CONNECTION_RETRIES:
                try:
                    sock.connect(potential_peer.address)
                    return sock, potential_peer
                except ConnectionRefusedError as exc:
                    logging.error("Connection refused with exception: %s", exc, exc_info=True)
                    retries = retry_delay(retries)
                except socket.timeout as exc:
                    logging.error("Connection timed out: %s", exc, exc_info=True)
                    retries = retry_delay(retries)
                except OSError as exc:
                    logging.error("Socket error: %s", exc, exc_info=True)
                    retries = retry_delay(retries)
                except Exception as exc:
                    logging.error("Unexpected error: %s", exc, exc_info=True)
                    retries = retry_delay(retries)
            logging.error(f"Failed to connect to {potential_peer.address} after {PEER_CONNECTION_RETRIES} max retry attempts")

            # Place the peer back onto the queue
            with peer_connection_lock:
                self.peers_able_to_connect.appendleft(potential_peer)
        return sock, None

    def request_piece_from_peer(self):
        # To implement, set up TCP connection with a peer I have
        # Request the piece
        # download and write the piece where it belongs
        # ensure I won't request the same piece again once successfully downloaded
        # things to keep in mind, if tcp connection fails I need to remove peer and get another, so store the list of peers from the request I made earlier, choose 5 to connect to and then add and remove
        # peers from the list until I need to check the tracker again
        while True:
            if self.finished_downloading_file():
                break
            with peer_connection_lock:
                current_port = USABLE_PORTS.pop()
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.bind((HOST, current_port))
                sock.settimeout(TEST_TIMEOUT)
                sock, connected_peer = self.find_peer_to_connect_to(sock)
                break
            except Exception as exc:
                # Place the port in use back in the list of available ports to be used and reraise the exception
                with peer_connection_lock:
                    USABLE_PORTS.append(current_port)
                time.sleep(DELAY_FOR_FINDING_PEER)
        if self.finished_downloading_file():
            return
        if connected_peer is None or connected_peer is False:
            raise ValueError("Connected peer is not set")
        retry = 0
        while retry < MAX_RETRIES_TO_CONNECT:
            try:
                # initiate and complete handshake, ensure peer speaks bit torrent protocol and has the file we want
                # always choked initially
                choked = self.initiate_complete_handshake(sock) # Always True initially
                # receive bitfield from peer, showing the pieces that the peer has that we can request
                bitfield_message = self.receive_bitfield(sock)
                connected_peer.set_bitfield(bitfield_message)
                self.send_unchoke_message(sock)
                self.send_interested_message(sock)
                # if handshake succeeded we are sharing with peer, we want to only stop sharing if we have been choked
                # for 30 seconds consecutively as such we will not share with them anymore
                sharing_with_peer = True
                while sharing_with_peer:
                    sharing_with_peer = not self.is_choked(sock, choked) # Check if choked for more than 30 seconds
                    if not sharing_with_peer:
                        break
                    # we are able to share with peer, so we are no longer choked
                    choked = False
                    piece = self.choose_piece_to_download(connected_peer.bitfield)
                    if piece is None: # peer does not have a piece we want so we can remove from the total list of peers
                        sharing_with_peer = False
                    while sharing_with_peer and piece.is_downloading():
                        if not choked:
                            # request: <len=0013><id=6><index><begin><length>
                            # continuously request blocks of the piece until finished downloading
                            sock.settimeout(30)
                            self.request_blocks_of_piece(sock, piece)
                        choked = self.read_message(sock, piece, connected_peer, choked)
                    if not piece.is_downloading():
                        if not self.place_piece_in_buffer(piece):
                            sharing_with_peer = False # peer isn't trust worthy since file sent incorrectly
                        else:
                            logging.debug(f'Downloaded piece from peer {connected_peer.address}')
            except TimeoutError as exc:
                logging.error(f"Timeout connecting to peer with ip:port, {connected_peer.IP}:{connected_peer.port} %s", exc, exc_info=True)
                retry += 1
                time.sleep(RETRY_AFTER)
            except socket.error as exc:
                logging.error(f"Unhandled Socket exception occurred during requesting piece from peer: %s",exc, exc_info=True)
                raise
            except Exception as exc:
                logging.error(f"Unhandled Exception occurred: %s", exc, exc_info=True)
                raise

        sock.close()

    def request_blocks_of_piece(self, sock: socket.socket, piece_downloading: Piece):
        while piece_downloading.backlog < MAXBACKLOG and piece_downloading.requested < piece_downloading.piece_length:
            block_size = BLOCK_BUFFER_SIZE
            remaining_piece = piece_downloading.piece_length - piece_downloading.requested
            if remaining_piece < BLOCK_BUFFER_SIZE:
                block_size = remaining_piece
            # request: <len=0013><id=6><index><begin><length>
            piece_request = self.construct_message_to_send(helpers.MessageLength.REQUEST,
                                                           helpers.MessageId.REQUEST, piece_downloading,
                                                           piece_downloading.requested, block_size)
            sock.sendall(piece_request)
            piece_downloading.backlog += 1
            piece_downloading.requested += block_size

    def read_full(self, sock: socket.socket, bytes_remaining: int):
        data = b''
        while bytes_remaining > 0:
            recv_data = sock.recv(bytes_remaining)
            if not recv_data:
                raise ConnectionAbortedError("Received no data from socket, aborted prematurely")
            data += recv_data
            bytes_remaining -= len(recv_data)

        return data


    def read_message(self, sock: socket.socket, piece_downloading: Piece, peer_connected: Outside_Peer, choked: bool):
        # implement waiting logic here to time out if peer doesn't send anything
        # piece: <len=0009+X><id=7><index><begin><block>
        # need to read more than block buffer size here, need to read 4 bytes for the length here, 1 byte for id, 4 bytes for integer, 4 bytes for begin
        additional_bytes = 4 + 1 + 4 + 4
        total_to_req = additional_bytes + BLOCK_BUFFER_SIZE
        # read here until we get the amount in a block
        data = self.read_full(sock, total_to_req)
        if data:
            message = Message(data)
            if message.message_id == helpers.MessageId.HAVE:
                piece_index = message.parse_piece_index_from_message()
                peer_connected.bitfield.set_piece(piece_index)
            if message.message_id == helpers.MessageId.PIECE:
                block = message.parse_piece_from_message()
                piece_downloading.place_block_in_buffer(block)
            if message.message_id == helpers.MessageId.CHOKE:
                choked = True
            if message.message_id == helpers.MessageId.UNCHOKE:
                choked = False

        return choked

    def choose_piece_to_download(self, peer_bitfield: Bitfield) -> Piece:
        # Choose pieces based on what is pulled off the queue and what the peer bitfield possesses
        piece = None
        for i in range(self.pieces_to_download_queue.qsize()):
            with downloading_lock:
                current_piece = self.pieces_to_download_queue.get()
                if peer_bitfield.has_piece(current_piece.index):
                    piece = current_piece
                    break
                else:
                    self.pieces_to_download_queue.put(current_piece)
        return piece

    # Have we been choked for more than 30 seconds without receiving an unchoke message
    def is_choked(self, sock: socket.socket, choked):
        choke_started = datetime.now()
        sock.settimeout(30)
        while choked:
            data = sock.recv(BLOCK_BUFFER_SIZE)
            try:
                message = Message(data)
            except Exception as exc:
                print(f'Exception {exc}')
                raise Exception(PICKING_PEER_ERROR)
            if message.message_id == helpers.MessageId.UNCHOKE:
                choked = False
                break
            time_passed = (choke_started - datetime.now()).total_seconds()
            if int(time_passed) > CHOKE_TIMEOUT:
                break
            time.sleep(CHECK_IFCHOKED_AFTER)
        return choked

    def construct_message_to_send(self, messagelen: int, messageId: helpers.MessageId, piece: Piece, messageBegin: int, messageLength: int):
        message_to_send = (messagelen.to_bytes(4, "big") +
                   messageId.value.to_bytes() +
                   piece.index.to_bytes(4, "big") +
                   messageBegin.to_bytes(4, "big") +
                   messageLength.to_bytes(4, "big"))

        return message_to_send

    def initiate_complete_handshake(self, sock):
        assert isinstance(sock, socket.socket)
        handshake = Handshake(self.torrent_details.lookup_dict['info_hash'], self.torrent_details.peerID)
        handshake_msg = helpers.get_handshake_message(handshake)
        try:
            sock.sendall(handshake_msg)
            data = sock.recv(helpers.HANDSHAKE_BUF_LEN)
            peer_handshake = Handshake()
            helpers.set_handshake_from_message(peer_handshake, data)
            if peer_handshake.info_hash != self.torrent_details.lookup_dict['info_hash']:
                raise ValueError(f'Expected infohash from peer but got {peer_handshake.info_hash}')
            else:
                return True  # maybe need peerid to track how long since last download
        except Exception as exc:
            print(f'unhandled exception occurred initiating handshake with peer: {exc}')
            sock.close()
            raise

    def write_finished_buffer_to_file(self):
        if self.pieces_to_download_queue.qsize() <= 0 and len(self.finished_buffer) == self.torrent_details.info_length:
            if os.path.exists(self.torrent_details.info_name):
                os.remove(self.torrent_details.info_name)
            with open(self.torrent_details.info_name, 'wb') as iso_file:
                iso_file.write(self.finished_buffer)
                print(f'Finished downloading file {self.torrent_details.info_name} and saved in folder {os.path.curdir}')
        else:
            print(f'File is not fully downloaded, queue size {self.pieces_to_download_queue.qsize()}'
                  f'\nlength of finished buffer to total length expected {len(self.finished_buffer)}:{self.torrent_details.info_length}')

    # -------------------------------------------
    # Driver method for peer to start downloading
    # Flow of torrent client
    # 1. Make a request to the torrent tracker to get a list of available peers we can download from, and who will potentially download from us
    # 2. After obtaining the list of peers, choose peers to make a request to for a specific piece
    # 3. Download the piece and place it in the appropriate place in the bytearray
    # 4. After all pieces are downloaded assemble the pieces in the correct order
    def start(self):
        # DEBUG - Turn this on to enable uploading pieces
        UPLOAD_FEATURE = False

        ping_tracker_for_info = True
        while True:
            if ping_tracker_for_info:
                self.retrieve_peers_from_tracker()
                ping_tracker_for_info = False
            try:
                threads_downloading = []
                for thd_count in range(NUM_OF_THREADS_FOR_DOWNLOAD):
                    thread = threading.Thread(target=self.request_piece_from_peer)
                    thread.start()
                    threads_downloading.append(thread)

                for thread in threads_downloading:
                    thread.join()

            except Exception as exc:
                if str(exc) != PICKING_PEER_ERROR:
                    raise
            if UPLOAD_FEATURE:
                events = self.socket_event_selector.select(timeout=None) # This is where some error occurring. Fix this tmr
                for selector_key, event_mask in events:
                    if selector_key.data is None:
                        self.accept_connections(selector_key.fileobj)
                    else:
                        self.service_connection(selector_key, event_mask)
            else:
                break
        self.write_finished_buffer_to_file()