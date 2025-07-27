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

# https://docs.python.org/3/library/socket.html

DEFAULT_PORT = 80
LOCAL_HOST = '127.0.0.1'
FILE_BUFFER_SIZE = 16384 # 16KB per file size, 16384 BYTES
TEST_FILE_BUFFER_SIZE = 256 # for testing purposes
PEERS_TO_SHARE_WITH = 5
TIMEOUT_FOR_PEER_DATA = 30

# Config key url parameters
PARAMETERS = 'url_parameters'
GET_TRACKER = 'get_tracker'


# Each peer/node is both a client and a server, should have way to send file and receive
# https://stackoverflow.com/questions/70962218/understanding-the-requisites-that-allow-bittorrent-peers-to-connect-to-each-othe
# https://docs.python.org/3/howto/sockets.html

class Outside_Peer:
    def __init__(self, IP=None, port=None):
        self.IP = IP
        self.port = port

class Peer:
    def __init__(self):
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket_event_selector = selectors.DefaultSelector()
        self.times_peers_last_sent = {}
        self.torrent_details = torrent.Torrent()
        self.check_peers_next = None
        self.other_peers_addresses = []

    def setup_server_sock(self):
        self.server_sock.bind((LOCAL_HOST, DEFAULT_PORT))
        self.server_sock.listen(PEERS_TO_SHARE_WITH)
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
                self.check_peers_next = (datetime.now() + timedelta(seconds=self.torrent_details.interval)).time()
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
            # TODO implement
        # self.populate_data_to_send(data)
        if mask & selectors.EVENT_WRITE and remote_addr in self.times_peers_last_sent:
            if data.outb:
                bytes_sent = socket_connection.send(data.outb[:TEST_FILE_BUFFER_SIZE])
                socket_connection.send(b'\n')
                print(f'Sent to client\n{data.outb[:bytes_sent]}')
                data.outb = data.outb[bytes_sent:]

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
            self.write_to_file(data.inb)
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

    def write_to_file(self, output_data):
        print("Implement writing to file logic here")



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



