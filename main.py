import peer as p

# example - https://github.com/gallexis/PyTorrent

if __name__ == '__main__':
    peer = p.Peer()

    # Run
    peer.setup_server_sock()
    print("Program Start")
    peer.start()

