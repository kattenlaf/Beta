import peer


def execute():
    print("Program Start")
    p = peer.Peer()
    p.setup_server_sock()
    p.start_peer()


if __name__ == '__main__':
    execute()
