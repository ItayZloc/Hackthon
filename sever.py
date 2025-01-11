import socket
import struct
import threading
import time

MAGIC_COOKIE = 0xabcddcba
MSG_TYPE_OFFER   = 0x02
MSG_TYPE_REQUEST = 0x03
MSG_TYPE_PAYLOAD = 0x04

# For a real LAN broadcast
BROADCAST_IP   = "255.255.255.255"
BROADCAST_PORT = 13117

SERVER_UDP_PORT = 20201
SERVER_TCP_PORT = 20202

UDP_PAYLOAD_CHUNK_SIZE = 1024
TCP_SEND_CHUNK_SIZE    = 4096

def broadcast_offers(stop_event):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while not stop_event.is_set():
            try:
                offer_msg = struct.pack("!IBHH",
                    MAGIC_COOKIE,
                    MSG_TYPE_OFFER,
                    SERVER_UDP_PORT,
                    SERVER_TCP_PORT
                )
                sock.sendto(offer_msg, (BROADCAST_IP, BROADCAST_PORT))
            except Exception as e:
                print(f"[Offer Thread] Error broadcasting offer: {e}")
            stop_event.wait(timeout=1.0)

def serve_one_udp_request(udp_sock, data, client_addr):
    if len(data) < 13:
        return
    magic_cookie, msg_type, file_size = struct.unpack("!IBQ", data)
    if magic_cookie != MAGIC_COOKIE or msg_type != MSG_TYPE_REQUEST:
        return

    print(f"[UDP] Got request from {client_addr}, file_size={file_size} bytes")
    if file_size == 0:
        return

    num_segments = (file_size + UDP_PAYLOAD_CHUNK_SIZE - 1) // UDP_PAYLOAD_CHUNK_SIZE
    bytes_left = file_size
    for segment_index in range(num_segments):
        header = struct.pack("!IBQQ",
            MAGIC_COOKIE,
            MSG_TYPE_PAYLOAD,
            num_segments,
            segment_index
        )
        chunk_size = min(UDP_PAYLOAD_CHUNK_SIZE, bytes_left)
        payload_data = b'\x00' * chunk_size
        packet = header + payload_data
        try:
            udp_sock.sendto(packet, client_addr)
        except Exception as e:
            print(f"[UDP] Error sending segment {segment_index}: {e}")
            break
        bytes_left -= chunk_size
        if bytes_left <= 0:
            break

    print(f"[UDP] Finished sending {file_size} bytes to {client_addr}")

def udp_request_listener(stop_event):
    udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    udp_sock.bind(("0.0.0.0", SERVER_UDP_PORT))
    print(f"[UDP] Listening for requests on port {SERVER_UDP_PORT}...")

    try:
        while not stop_event.is_set():
            data, client_addr = udp_sock.recvfrom(2048)
            threading.Thread(
                target=serve_one_udp_request,
                args=(udp_sock, data, client_addr),
                daemon=True
            ).start()
    except OSError:
        print("[UDP] Socket closed, shutting down UDP listener.")
    finally:
        udp_sock.close()

def serve_one_tcp_client(client_sock, client_addr):
    print(f"[TCP] Accepted connection from {client_addr}")
    try:
        file_size_str = ""
        while True:
            chunk = client_sock.recv(1024)
            if not chunk:
                break
            file_size_str += chunk.decode()
            if "\n" in file_size_str:
                break

        file_size = int(file_size_str.strip() or "0")
        print(f"[TCP] Client requests {file_size} bytes")

        bytes_left = file_size
        while bytes_left > 0:
            chunk_size = min(TCP_SEND_CHUNK_SIZE, bytes_left)
            data_to_send = b'\x00' * chunk_size
            client_sock.sendall(data_to_send)
            bytes_left -= chunk_size

        print(f"[TCP] Finished sending {file_size} bytes to {client_addr}")
    except Exception as e:
        print(f"[TCP] Error during transfer: {e}")
    finally:
        client_sock.close()
        print(f"[TCP] Connection closed with {client_addr}")

def tcp_request_listener(stop_event):
    tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tcp_sock.bind(("0.0.0.0", SERVER_TCP_PORT))
    tcp_sock.listen(5)
    print(f"[TCP] Listening for connections on port {SERVER_TCP_PORT}...")

    try:
        while not stop_event.is_set():
            client_sock, client_addr = tcp_sock.accept()
            threading.Thread(
                target=serve_one_tcp_client,
                args=(client_sock, client_addr),
                daemon=True
            ).start()
    except OSError:
        print("[TCP] Socket closed, shutting down TCP listener.")
    finally:
        tcp_sock.close()

def main():
    stop_event = threading.Event()

    offer_thread = threading.Thread(target=broadcast_offers, args=(stop_event,), daemon=True)
    offer_thread.start()

    udp_thread = threading.Thread(target=udp_request_listener, args=(stop_event,), daemon=True)
    udp_thread.start()

    tcp_thread = threading.Thread(target=tcp_request_listener, args=(stop_event,), daemon=True)
    tcp_thread.start()

    print("[Server] Running on LAN. Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("[Server] Stopping...")

    stop_event.set()

    # Force the UDP and TCP sockets to exit their blocking calls
    try:
        # Unblock UDP
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b"stop", ("127.0.0.1", SERVER_UDP_PORT))
        # Unblock TCP
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect(("127.0.0.1", SERVER_TCP_PORT))
    except:
        pass

    print("[Server] Exiting.")

if __name__ == "__main__":
    main()
