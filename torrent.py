import torrent_parser
import helpers

TORRENT_FILE_PATH_KEY = 'torrent_file_path'

class Torrent:
    def __init__(self):
        try:
            config_yaml = helpers.get_config_yaml()
            data = torrent_parser.parse_torrent_file(config_yaml.get(TORRENT_FILE_PATH_KEY))
            print(data)
        except Exception as exc:
            print(f'Unexpected exception parsing torrent file')
            return

        self.announce = data['announce']
        self.info_length = data['info']['length']
        self.info_name = data['info']['name']
        self.info_piece_length = data['info']['piece length']
        self.info_pieces = data['info']['pieces']
        self.url_list = data['url-list']
        # self.print_torrent_data()

    def print_torrent_data(self):
        print(self.announce)
        print(self.info_length)
        print(self.info_name)
        print(self.url_list)
        print(self.info_piece_length)
        for piece in self.info_pieces:
            print(pieces)