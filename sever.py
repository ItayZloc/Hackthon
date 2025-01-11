import socket
import struct
import threading
import time

# Magic cookie and message types (do not modify)
MAGIC_COOKIE = 0xabcddcba
MSG_TYPE_OFFER = 0x2
MSG_TYPE_REQUEST = 0x3
MSG_TYPE_PAYLOAD = 0x4

# Broadcast port where the server sends “offer” messages (the client must also listen on this port).
BROADCAST_PORT = 13117

# Frequency (in seconds) at which the server broadcasts its “offer” messages.
BROADCAST_INTERVAL = 1.0

# Size of each UDP payload chunk. Adjust as needed.
UDP_PAYLOAD_CHUNK = 10000

# Buffer size for receiving UDP packets.
UDP_BUFFER_SIZE = 65535

def broadcast_offers(udp_port, tcp_port, stop_event):
    """
    Continuously broadcasts an 'offer' message at fixed intervals.
    """
    broadcaster = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    broadcaster.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    # Build the offer packet: [magic_cookie (4 bytes)] [msg_type (1 byte)] [server_udp_port (2 bytes)] [server_tcp_port (2 bytes)]
    offer_packet = struct.pack("!I B H H", MAGIC_COOKIE, MSG_TYPE_OFFER, udp_port, tcp_port)

    while not stop_event.is_set():
        try:
            broadcaster.sendto(offer_packet, ("<broadcast>", BROADCAST_PORT))
        except:
            pass
        time.sleep(BROADCAST_INTERVAL)

    broadcaster.close()

def handle_tcp_connection(client_socket):
    """
    Receives the file-size request over TCP and responds with exactly that many bytes.
    """
    try:
        request_data = b""
        while b"\n" not in request_data:
            chunk = client_socket.recv(1024)
            if not chunk:
                return
            request_data += chunk

        requested_size = int(request_data.strip().decode())
        dummy_payload = b"\x00" * requested_size
        client_socket.sendall(dummy_payload)
    except:
        pass
    finally:
        client_socket.close()

def tcp_listener(tcp_socket):
    """
    Waits for incoming TCP connections; spawns a new thread for each request.
    """
    while True:
        client_socket, _ = tcp_socket.accept()
        thread = threading.Thread(target=handle_tcp_connection, args=(client_socket,))
        thread.daemon = True
        thread.start()

def send_udp_payload(udp_socket, client_address, requested_size):
    """
    Sends UDP payload packets in sequence until the requested size is met.
    """
    total_segments = (requested_size + UDP_PAYLOAD_CHUNK - 1) // UDP_PAYLOAD_CHUNK
    bytes_sent = 0
    current_segment = 0

    while bytes_sent < requested_size:
        current_segment += 1
        end_pos = min(bytes_sent + UDP_PAYLOAD_CHUNK, requested_size)
        payload_data = b"\x00" * (end_pos - bytes_sent)

        # Packet format: [magic_cookie (4)] [msg_type (1)] [total_segments (8)] [current_segment (8)] [payload ...]
        header = struct.pack("!I B Q Q", MAGIC_COOKIE, MSG_TYPE_PAYLOAD, total_segments, current_segment)
        try:
            udp_socket.sendto(header + payload_data, client_address)
        except:
            break

        bytes_sent = end_pos

def udp_listener(udp_socket):
    """
    Listens for UDP "request" packets from the client and spawns a thread to send the payload.
    """
    while True:
        data, addr = udp_socket.recvfrom(UDP_BUFFER_SIZE)
        if len(data) < 5:
            continue

        cookie = struct.unpack("!I", data[:4])[0]
        if cookie != MAGIC_COOKIE:
            continue

        msg_type = data[4]
        if msg_type == MSG_TYPE_REQUEST and len(data) >= 13:
            requested_size = struct.unpack("!Q", data[5:13])[0]
            thread = threading.Thread(target=send_udp_payload, args=(udp_socket, addr, requested_size))
            thread.daemon = True
            thread.start()

def main():
    print("Server started, listening on IP 0.0.0.0")

    # Create a TCP socket on a dynamic (ephemeral) port
    tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_socket.bind(("", 0))  # Port 0 => OS chooses an available port
    tcp_socket.listen()
    server_tcp_port = tcp_socket.getsockname()[1]

    # Create a UDP socket on a dynamic (ephemeral) port
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.bind(("", 0))  # Port 0 => OS chooses an available port
    server_udp_port = udp_socket.getsockname()[1]

    # Event to stop broadcasting (useful if you ever want to shut down gracefully)
    stop_event = threading.Event()

    # Start broadcasting offers
    broadcaster_thread = threading.Thread(
        target=broadcast_offers,
        args=(server_udp_port, server_tcp_port, stop_event)
    )
    broadcaster_thread.daemon = True
    broadcaster_thread.start()

    # Start TCP listener thread
    tcp_thread = threading.Thread(target=tcp_listener, args=(tcp_socket,))
    tcp_thread.daemon = True
    tcp_thread.start()

    # Start UDP listener thread
    udp_thread = threading.Thread(target=udp_listener, args=(udp_socket,))
    udp_thread.daemon = True
    udp_thread.start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_event.set()
        print("\nServer shutting down...")

if __name__ == "__main__":
    main()
