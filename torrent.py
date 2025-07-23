import os
import secrets
import torrent_parser
import hashlib

import helpers

TORRENT_FILE_PATH_KEY = 'torrent_file_path'

class Torrent:
    def __init__(self):
        try:
            config_yaml = helpers.get_config_yaml()
            data = torrent_parser.parse_torrent_file(config_yaml.get(TORRENT_FILE_PATH_KEY))
        except Exception as exc:
            print(f'Unexpected exception parsing torrent file: {exc}')
            return

        self.announce = data['announce']
        self.info_length = data['info']['length']
        self.info_name = data['info']['name']
        self.info_piece_length = data['info']['piece length']
        self.info_pieces = data['info']['pieces']
        self.url_list = data['url-list']
        self.peerID = secrets.token_bytes(20)
        sha1_hash = hashlib.sha1()
        self.info_hash = helpers.sha1_hash_string(data['info'])

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