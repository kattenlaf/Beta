import peer as p

# example - https://github.com/gallexis/PyTorrent

if __name__ == '__main__':
    peer = None
    # Setup
    try:
        peer = p.Peer()
    except Exception as exc:
        print(f"Failure using torrent parser to open torrent file with exception:{exc}")

    # Run
    peer.setup_server_sock()
    print("Program Start")
    peer.start()

