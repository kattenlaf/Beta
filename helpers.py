import hashlib
import yaml
from enum import IntEnum

from peer import PSTRLEN_BYTES_LEN, PSTR_BYTES_LEN, RESERVED_BYTES_LEN, INFO_HASH_BYTES_LEN, PEER_ID_BYTES_LEN

CONFIG_FILE_PATH = 'config.yaml'
PORT = 80

class MessageLength(IntEnum):
    BITFIELD = 1 # 1 + X
    UNCHOKE = 1
    CHOKE = 1
    INTERESTED = 1
    UNINTERESTED = 1
    HAVE = 5
    PIECE = 9  # 9 + X
    REQUEST = 13
    CANCEL = 13

class MessageId(IntEnum):
    CHOKE = 0
    UNCHOKE = 1
    INTERESTED = 2
    UNINTERESTED = 3
    HAVE = 4
    BITFIELD = 5
    REQUEST = 6
    PIECE = 7
    CANCEL = 8

class Pieces_Download_State(IntEnum):
    NOT_STARTED = 0
    IN_PROGRESS = 1
    COMPLETED = 2

# https://www.youtube.com/watch?v=9Z2U3HF3iD4&ab_channel=RDCLive
# handshake: <pstrlen><pstr><reserved><info_hash><peer_id> - https://wiki.theory.org/BitTorrentSpecification
def get_handshake_message(handshake):
    handshake_msg = bytearray(len(handshake.pstr) + 49)
    pos = 0
    handshake_msg[pos:], pos = handshake.pstrlen, pos + len(handshake.pstrlen)
    handshake_msg[pos:], pos = handshake.pstr, pos + len(handshake.pstr)
    handshake_msg[pos:], pos = handshake.reserved, pos + len(handshake.reserved)
    handshake_msg[pos:], pos = handshake.info_hash, pos + len(handshake.info_hash)
    handshake_msg[pos:] = handshake.peer_id

    return handshake_msg

# Method to parse
def set_handshake_from_message(handshake, message):
    pos = 0
    handshake.pstrlen, pos = message[pos:pos+PSTRLEN_BYTES_LEN], pos+PSTRLEN_BYTES_LEN
    handshake.pstr, pos = message[pos:pos+PSTR_BYTES_LEN], pos+PSTR_BYTES_LEN
    handshake.reserved, pos = message[pos:pos+RESERVED_BYTES_LEN], pos+RESERVED_BYTES_LEN
    handshake.info_hash, pos = message[pos:pos+INFO_HASH_BYTES_LEN], pos+INFO_HASH_BYTES_LEN
    handshake.peer_id, pos = message[pos:pos+PEER_ID_BYTES_LEN], pos+INFO_HASH_BYTES_LEN

def get_config_yaml():
    try:
        with open(CONFIG_FILE_PATH, 'r') as file:
            yaml_data = yaml.full_load(file)
            return yaml_data
    except Exception as exc:
        print(f'Unexpected exception opening yaml: {exc}')