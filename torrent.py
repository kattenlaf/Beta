import socket

import os
import requests
import secrets
import torrent_parser
import hashlib
from bcoding import bencode, bdecode

import helpers

TORRENT_FILE_PATH_KEY = 'torrent_file_path'
PIECES_BYTES_LENGTH = 20

class Torrent:
    def __init__(self):
        # Time to check for more peers
        self.interval = None
        try:
            config_yaml = helpers.get_config_yaml()
            with open(config_yaml.get(TORRENT_FILE_PATH_KEY), 'rb') as f:
                file_read = f.read()
                data = bdecode(file_read)

        except Exception as exc:
            print(f'Unexpected exception parsing torrent file: {exc}')
            return

        self.announce = data['announce']
        self.info_length = data['info']['length']
        self.info_name = data['info']['name']
        self.info_piece_length = data['info']['piece length']
        self.info_pieces = set([data['info']['pieces'][i:i+PIECES_BYTES_LENGTH] for i in range(0, len(data['info']['pieces']), PIECES_BYTES_LENGTH)])
        self.url_list = data['url-list']
        self.peerID = secrets.token_bytes(20)
        self.info_hash = hashlib.sha1(bencode(data['info'])).hexdigest()
        self.lookup_dict = {
            'info_hash': bytes.fromhex(self.info_hash),
            'peer_id':self.peerID,
            'port':6881,
            'uploaded':'0',
            'downloaded':'0',
            'compact':'1',
            'left':str(self.info_length)
        }
        self.print_torrent_data()

    def print_torrent_data(self):
        print(self.announce)
        print(self.info_length)
        print(self.info_name)
        print(self.url_list)
        print(self.info_piece_length)
        print(self.peerID)
        for piece in self.info_pieces:
            print(piece)


    def get_peers_from_response(self, response):
        assert isinstance(response, requests.Response)
        decoded = bdecode(response.content)
        self.interval = decoded['interval']
        return decoded['peers']
