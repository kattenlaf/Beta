import types

import socket, socketserver
import threading
import multiprocessing
import selectors # https://realpython.com/python-sockets/
from datetime import datetime

# https://docs.python.org/3/library/socket.html

DEFAULT_PORT = 80
LOCAL_HOST = '127.0.0.1'
FILE_BUFFER_SIZE = 16000 # 16KB per file size, 16000 BYTES
TEST_FILE_BUFFER_SIZE = 128 # for testing purposes
PEERS_TO_SHARE_WITH = 5
TIMEOUT_FOR_PEER_DATA = 30

# Each peer/node is both a client and a server, should have way to send file and receive
# https://stackoverflow.com/questions/70962218/understanding-the-requisites-that-allow-bittorrent-peers-to-connect-to-each-othe
# https://docs.python.org/3/howto/sockets.html

class Peer:
    def __init__(self):
        self.other_peers_address = [None for i in range(PEERS_TO_SHARE_WITH)]
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket_event_selector = selectors.DefaultSelector()
        self.times_peers_last_sent = {}

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
        client_socket = key.fileobj
        data = key.data
        if mask & selectors.EVENT_READ:
            # read data from the socket
            # data received here will be different
            data_received = client_socket.recv(TEST_FILE_BUFFER_SIZE)
            if data_received:
                self.times_peers_last_sent[client_socket.getpeername()] = datetime.now()
                data.outb += data_received
            else:
                # Unregister the connection to that client if 30 seconds has past since it last sent a packet
                time_of_last_packet = self.times_peers_last_sent[client_socket.getpeername()]
                if (datetime.now() - time_of_last_packet).total_seconds() > TIMEOUT_FOR_PEER_DATA:
                    self.socket_event_selector.unregister(client_socket)
                    client_socket.close()
        if mask & selectors.EVENT_WRITE:
            if data.outb:
                bytes_sent = client_socket.send(data.outb[:TEST_FILE_BUFFER_SIZE])
                print(f'Sent to client\n{data.outb[:bytes_sent]}')
                data.outb = data.outb[bytes_sent:]

    def read_data(self, socket):
        data = socket.recv(FILE_BUFFER_SIZE)
        if data:
            socket.send(b'Data Received')
        else:
            # probably keep a timer dictionary object of this sockets address so if 30 seconds have passed and it hasn't sent data we kick it off
            self.socket_event_selector.unregister(socket)
            socket.close()

    def send_data(self, conn, data):
        # https://stackoverflow.com/questions/34252273/what-is-the-difference-between-socket-send-and-socket-sendall
        conn.sendall(data)


    def start_peer(self):
        while True:
            events = self.socket_event_selector.select(timeout=None)
            for selector_key, event_mask in events:
                if selector_key.data is None:
                    self.accept_connections(selector_key.fileobj)
                else:
                    self.service_connection(selector_key, event_mask)

