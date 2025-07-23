import yaml

CONFIG_FILE_PATH = 'config.yaml'

def get_config_yaml():
    try:
        with open(CONFIG_FILE_PATH, 'r') as file:
            yaml_data = yaml.full_load(file)
            return yaml_data
    except Exception as exc:
        print(f'Unexpected exception opening yaml: {exc}')