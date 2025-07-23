import hashlib
import yaml

CONFIG_FILE_PATH = 'config.yaml'
PORT = 80

def get_config_yaml():
    try:
        with open(CONFIG_FILE_PATH, 'r') as file:
            yaml_data = yaml.full_load(file)
            return yaml_data
    except Exception as exc:
        print(f'Unexpected exception opening yaml: {exc}')

def sha1_hash_string(decoded_string):
    sha1_hash = hashlib.sha1()
    encoded_string = str(decoded_string).encode('utf-8')
    sha1_hash.update(encoded_string)
    return sha1_hash.hexdigest()