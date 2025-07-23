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

class Peer:
    def __init__(self):
        self.other_peers_address = [None for i in range(PEERS_TO_SHARE_WITH)]
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket_event_selector = selectors.DefaultSelector()
        self.times_peers_last_sent = {}
        self.torrent_details = torrent.Torrent()

    def retrieve_peers_from_tracker(self):
        parameters = {}
        config_yaml = helpers.get_config_yaml()
        url_parameters = config_yaml.get(PARAMETERS)
        for param in url_parameters[GET_TRACKER]:
            parameters[param] = self.torrent_details.lookup_dict[param]
        try:
            # https://stackoverflow.com/questions/58282768/how-to-send-requests-to-bittorrent-tracker-using-python-requests
            response = requests.get(self.torrent_details.announce, params=parameters) # torrent not founded error, TODO fix tomorrow
            print(response)
        except requests.exceptions.RequestException as exc:
            print(f'Exception making request to tracker: {exc}')
            # retry here potentially
        except Exception as e:
            print(f'Unhandled exception when making request to tracker: {exc}')

    def setup_server_sock(self):
        self.server_sock.bind((LOCAL_HOST, DEFAULT_PORT))
        self.server_sock.listen(PEERS_TO_SHARE_WITH)
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
        while True:
            if ping_tracker_for_info:
                self.retrieve_peers_from_tracker()
            events = self.socket_event_selector.select(timeout=None)
            for selector_key, event_mask in events:
                if selector_key.data is None:
                    self.accept_connections(selector_key.fileobj)
                else:
                    self.service_connection(selector_key, event_mask)

