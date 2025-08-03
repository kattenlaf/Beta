import time

from _datetime import timedelta

import helpers
import torrent

import types
import socket, socketserver
import threading
import multiprocessing
import selectors # https://realpython.com/python-sockets/
from datetime import datetime
import urllib
import requests
import struct
from collections import defaultdict

# https://docs.python.org/3/library/socket.html

DEFAULT_PORT = 80
LOCAL_HOST = '127.0.0.1'
FILE_BUFFER_SIZE = 16384 # 16KB per file size, 16384 BYTES
TEST_FILE_BUFFER_SIZE = 256 # for testing purposes
MAX_PEERS_TO_SHARE_WITH = 5
TIMEOUT_FOR_PEER_DATA = 30
SOCKET_CONNECT_TIMEOUT = 10
MAX_RETRIES_TO_CONNECT = 2
RETRY_AFTER = 10
# Choke related constants
CHECK_IFCHOKED_AFTER = 10
CHOKE_TIMEOUT = 30

# Config key url parameters
PARAMETERS = 'url_parameters'
GET_TRACKER = 'get_tracker'

# Constants for expected bytes in handshake messages
PSTRLEN_BYTES_LEN = 1
PSTR_BYTES_LEN = 19
RESERVED_BYTES_LEN = 8
INFO_HASH_BYTES_LEN = 20
PEER_ID_BYTES_LEN = 20
HANDSHAKE_BUF_LEN = PSTRLEN_BYTES_LEN+PSTR_BYTES_LEN+RESERVED_BYTES_LEN+INFO_HASH_BYTES_LEN+PEER_ID_BYTES_LEN



# Each peer/node is both a client and a server, should have way to send file and receive
# https://stackoverflow.com/questions/70962218/understanding-the-requisites-that-allow-bittorrent-peers-to-connect-to-each-othe
# https://docs.python.org/3/howto/sockets.html

class Outside_Peer:
    def __init__(self, IP=None, port=None):
        self.IP = IP
        self.port = port

    def __eq__(self, other):
        if self.IP == other.IP and self.port == other.port:
            return True
        return False

class Handshake:
    def __init__(self, info_hash=None, peer_id=None):
        self.pstr = 'BitTorrent protocol'
        self.pstrlen = len(self.pstr).to_bytes(1, byteorder='big')
        self.reserved = bytes(8) #\x00\x00...\x00
        self.info_hash = info_hash
        self.peer_id = peer_id

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
    def __init__(self, payload, messageId):
        # length here should probably be different, maybe misunderstood documentation?
        self.length = len(payload)
        self.message_id = messageId
        self.payload = payload
        self.message = self.length.to_bytes(4, "big") + int(self.message_id).to_bytes(1, "big") + self.payload

    def __init__(self, recv_data):
        pos = 0
        self.length, pos = int(recv_data[pos:LENGTH_BYTES]), pos+LENGTH_BYTES
        self.message_id, pos = helpers.MessageId(int(recv_data[pos:ID_BYTES])), pos+ID_BYTES
        self.payload = recv_data[pos:]
        self.message = self.length.to_bytes(4, "big") + int(self.message_id).to_bytes(1, "big") + self.payload

class Piece:
    def __init__(self):
        self.index = 0
        self.buffer = bytearray(256)
        self.downloaded = 0
        self.requested = 0
        self.backlog = 0

class Peer:
    def __init__(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket_event_selector = selectors.DefaultSelector()
        self.times_peers_last_sent = {}
        self.torrent_details = torrent.Torrent()
        self.handshake_msg = Handshake(self.torrent_details.info_hash, self.torrent_details.peerID)
        self.check_peers_next = None
        self.other_peers_addresses = []
        self.peers_available_for_use = defaultdict(lambda: False) # peers our Peer is currently using, if value is set to True, the Peer is currently connected via a socket already
        self.current_peers_available_for_use_pos = 0
        self.downloaded_pieces = set()

    def setup_server_sock(self):
        self.server_sock.bind((LOCAL_HOST, DEFAULT_PORT))
        self.server_sock.listen(MAX_PEERS_TO_SHARE_WITH)
        self.server_sock.setblocking(False)
        self.socket_event_selector.register(self.server_sock, selectors.EVENT_READ, data=None)

    def retrieve_peers_from_tracker(self):
        if self.should_retrieve_peers():
            url_parameter_node = helpers.get_config_yaml().get(PARAMETERS)
            parameters = self.get_parameters_for_requests(url_parameter_node, GET_TRACKER)
            try:
                response = requests.get(self.torrent_details.announce, params=parameters)
                peers_from_response = self.torrent_details.get_peers_from_response(response)
                self.other_peers_addresses = self.unmarshal(peers_from_response)
                self.set_connected_peers()
                self.check_peers_next = datetime.now() + timedelta(seconds=self.torrent_details.interval)
            except requests.exceptions.RequestException as exc:
                print(f'Exception making request to tracker: {exc}')
                # retry here potentially
            except Exception as e:
                print(f'Unhandled exception when making request to tracker: {e}')

    def set_connected_peers(self):
        num_of_peers_to_add = MAX_PEERS_TO_SHARE_WITH - len(self.peers_available_for_use)
        for i in range(self.current_peers_available_for_use_pos, self.current_peers_available_for_use_pos + num_of_peers_to_add):
            if self.current_peers_available_for_use_pos >= MAX_PEERS_TO_SHARE_WITH:
                return
            if i < len(self.other_peers_addresses):
                peer_to_add = self.other_peers_addresses[i]
                if peer_to_add not in self.peers_available_for_use:
                    self.peers_available_for_use[peer_to_add] = False
                    self.current_peers_available_for_use_pos += 1

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
            # clear inb after writing data received wherever, it will keep incrementing
            # TODO implement
        self.populate_data_to_send(data)
        if mask & selectors.EVENT_WRITE and remote_addr in self.times_peers_last_sent:
            if data.outb:
                bytes_sent = socket_connection.send(data.outb[:TEST_FILE_BUFFER_SIZE])
                socket_connection.send(b'\n')
                print(f'Sent to client\n{data.outb[:bytes_sent]}')
                data.outb = data.outb[bytes_sent:]

    def request_piece_from_peer(self):
        # To implement, set up TCP connection with a peer I have
        # Request the piece
        # download and write the piece where it belongs
        # ensure I won't request the same piece again once successfully downloaded
        # things to keep in mind, if tcp connection fails I need to remove peer and get another, so store the list of peers from the request I made earlier, choose 5 to connect to and then add and remove
        # peers from the list until I need to check the tracker again
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        peer_to_connect_to = self.select_peer_to_connect()
        if peer_to_connect_to is False:
            raise ValueError("There are no peers available to be connected to")
        assert isinstance(Outside_Peer, peer_to_connect_to)
        retry = 0
        while retry < MAX_RETRIES_TO_CONNECT:
            try:
                sock.connect(peer_to_connect_to.IP, peer_to_connect_to.port)
                # initiate and complete handshake, ensure peer speaks bit torrent protocol and has the file we want
                # always choked initially
                choked = self.initiate_complete_handshake(sock)
                if not choked:
                    # this means for some reason the handshake didn't succeed if we are not choked
                    # remove the peer and return so we will try another peer later?
                    return
                # if handshake succeeded we are sharing with peer, we want to only stop sharing if we have been choked
                # for 30 seconds consecutively as such we will not share with them anymore
                sharing_with_peer = True
                while sharing_with_peer:
                    # keep sharing until they don't share with us for some time
                    sharing_with_peer = self.check_if_still_choked(sock, choked)
                    # make a request for a piece
                    # then download the piece if received


            except TimeoutError as exc:
                print(f"Timeout connecting to peer with ip:port, {peer_to_connect_to.IP}:{peer_to_connect_to.port}")
                retry += 1
                time.sleep(RETRY_AFTER)
            except socket.error as exc:
                print(f"Unhandled Socket exception occurred exception:{exc}")
                raise

    # request: <len=0013><id=6><index><begin><length>
    def build_request_piece_message(self, index, begin, length):
        # Complete tmr
        message = Message(payload, messageId)


    # maybe rename method, may not always just be checking if choked here. any message could be sent
    def check_if_still_choked(self, sock, choked):
        choke_started = datetime.now()
        while choked:
            # recv message from peer here
            # perhaps may need to do other indepth checks on the message, we may be choked but they could request a file
            # or send some other message
            data = sock.recv(FILE_BUFFER_SIZE)
            message = Message(data)
            choked = self.handle_message_received(message)
            if int((choke_started - datetime.now()).total_seconds()) > CHOKE_TIMEOUT:
                break
            time.sleep(CHECK_IFCHOKED_AFTER)
        if choked:
            print("Remove peer from list we will share with")
        return choked

    # https://wiki.theory.org/BitTorrentSpecification - Messages
    def handle_message_received(self, message: Message):
        if message.message_id == helpers.MessageId.UNCHOKE:
            return True
        if message.message_id == helpers.MessageId.HAVE:
            # should length be divided by 8 for bytes?
            piece_index = message.payload[5:message.length]
            return piece_index
        if message.message_id == helpers.MessageId.PIECE:
            if not self.write_block_to_piece(message):
                raise IOError("Error writing payload received to block")
            return

        return False

    def write_block_to_piece(self, message: Message):
        # piece: <len=0009+X><id=7><index><begin><block>
        # TODO clean up magic numbers for this
        block_length = message.length - 9
        index = int(message.payload[6])
        begin = int(message.payload[7])
        block = message.payload[8:]

        return True

    def initiate_complete_handshake(self, sock):
        assert isinstance(sock, socket.socket)
        handshake = Handshake(self.torrent_details.info_hash, self.torrent_details.peerID)
        message = helpers.get_handshake_message(handshake)
        try:
            sock.sendall(message)
            data = sock.recv(HANDSHAKE_BUF_LEN)
            if len(data) != HANDSHAKE_BUF_LEN:
                raise ValueError('peer sent malformed handshake message')
            else:
                peer_handshake = Handshake()
                helpers.set_handshake_from_message(peer_handshake, data)
                return peer_handshake.peer_id != None
        except Exception as exc:
            print(f'unhandled exception occurred initiating handshake with peer: {exc}')
            raise

    def select_peer_to_connect(self):
        for peer in self.peers_available_for_use.keys():
            if self.peers_available_for_use[peer] != False:
                return peer

        return False

    # TODO: Implement method to periodically check and remove peers we get no packets from?

    def populate_data_to_send(self, data):
        # Solution to find what bytes/file chunk to send here
        data.outb = b"Hello from server"

    def read_data(self, socket_connection, data):
        data_received = socket_connection.recv(TEST_FILE_BUFFER_SIZE)
        data_received = self.format_data(data_received, selectors.EVENT_READ)
        socket_connection_address = socket_connection.getpeername()
        if data_received:
            self.times_peers_last_sent[socket_connection_address] = datetime.now()
            data.inb += data_received
            data.inb += b"\n"
        else:
            self.write_to_file(data)
            # Unregister the connection to that client if 30 seconds has past since it last sent a packet
            time_of_last_packet = self.times_peers_last_sent[socket_connection_address]
            if (datetime.now() - time_of_last_packet).total_seconds() > TIMEOUT_FOR_PEER_DATA:
                self.socket_event_selector.unregister(socket_connection)
                self.times_peers_last_sent.pop(socket_connection_address)
                socket_connection.close()

    def format_data(self, data, type):
        if type == selectors.EVENT_READ:
            data = data.rstrip()

        return data

    def write_to_file(self, data):
        print("Implement writing to file logic here")
        received_data = data.inb
        data.inb = b""
        # Implement writing the
        print(f"received {received_data} from peer")




    def start(self):
        ping_tracker_for_info = True
        # ping tracker for peer info only if we have exceeded the duration
        while True:
            if ping_tracker_for_info:
                self.retrieve_peers_from_tracker()
            events = self.socket_event_selector.select(timeout=None) # This is where some error occurring. Fix this tmr
            for selector_key, event_mask in events:
                if selector_key.data is None:
                    self.accept_connections(selector_key.fileobj)
                else:
                    self.service_connection(selector_key, event_mask)



