import socket
import struct
import threading
import time

MAGIC_COOKIE = 0xabcddcba
OFFER_MESSAGE_TYPE = 0x2
REQUEST_MESSAGE_TYPE = 0x3
PAYLOAD_MESSAGE_TYPE = 0x4
BROADCAST_PORT = 13117

# ---------------- NEW TIMEOUT VARIABLE ----------------
TIMEOUT = 30.0

def get_user_input():
    file_size = int(input("Enter file size in bytes: "))
    tcp_connections = int(input("Enter number of TCP connections: "))
    udp_connections = int(input("Enter number of UDP connections: "))
    return file_size, tcp_connections, udp_connections

def listen_for_offers():
    """
    Listens for broadcast offers from any server on the LAN.
    Binds to 0.0.0.0:13117 so we can receive broadcast packets.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        udp_socket.bind(('0.0.0.0', BROADCAST_PORT))
        print("Client started, listening for offers on the LAN...")

        while True:
            data, addr = udp_socket.recvfrom(1024)
            try:
                if len(data) >= 9:
                    magic_cookie, msg_type, udp_port, tcp_port = struct.unpack('!IBHH', data[:9])
                    if magic_cookie == MAGIC_COOKIE and msg_type == OFFER_MESSAGE_TYPE:
                        print(f"Received offer from {addr[0]} (UDP: {udp_port}, TCP: {tcp_port})")
                        return addr[0], udp_port, tcp_port
            except struct.error:
                print("Malformed offer packet received.")

def tcp_transfer(server_ip, tcp_port, file_size):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp_socket:
            # ---------------- UPDATED TIMEOUT ----------------
            tcp_socket.settimeout(TIMEOUT)
            
            tcp_socket.connect((server_ip, tcp_port))
            tcp_socket.sendall(str(file_size).encode() + b'\n')

            start_time = time.time()
            received_data = 0
            while received_data < file_size:
                chunk = tcp_socket.recv(4096)
                if not chunk:
                    break
                received_data += len(chunk)

            end_time = time.time()
            total_time = end_time - start_time
            print(f"TCP Transfer: {received_data} / {file_size} bytes in {total_time:.2f} seconds")
    except Exception as e:
        print(f"Error during TCP transfer: {e}")

def udp_transfer(server_ip, udp_port, file_size):
    """
    Sends the 'request' packet and then receives all payload packets.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            # ---------------- UPDATED TIMEOUT ----------------
            udp_socket.settimeout(TIMEOUT)
            
            request_packet = struct.pack('!IBQ', MAGIC_COOKIE, REQUEST_MESSAGE_TYPE, file_size)
            udp_socket.sendto(request_packet, (server_ip, udp_port))

            start_time = time.time()

            total_segments = None
            received_segments = 0
            total_bytes_received = 0

            while total_segments is None or received_segments < total_segments:
                try:
                    data, _ = udp_socket.recvfrom(2048)
                except socket.timeout:
                    print("UDP timed out waiting for more packets.")
                    break

                if len(data) < 21:
                    continue

                magic_cookie, msg_type, num_segments, seg_index = struct.unpack('!IBQQ', data[:21])
                payload = data[21:]
                if magic_cookie == MAGIC_COOKIE and msg_type == PAYLOAD_MESSAGE_TYPE:
                    if total_segments is None:
                        total_segments = num_segments
                    received_segments += 1
                    total_bytes_received += len(payload)

            end_time = time.time()
            total_time = end_time - start_time
            print(f"UDP Transfer: {received_segments} segments, {total_bytes_received} bytes in {total_time:.2f} sec")
    except Exception as e:
        print(f"Error during UDP transfer: {e}")

def start_speed_test(server_ip, tcp_port, udp_port, file_size, tcp_connections, udp_connections):
    threads = []
    for _ in range(tcp_connections):
        t = threading.Thread(target=tcp_transfer, args=(server_ip, tcp_port, file_size))
        threads.append(t)
    for _ in range(udp_connections):
        t = threading.Thread(target=udp_transfer, args=(server_ip, udp_port, file_size))
        threads.append(t)

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print("All transfers complete.")

def main():
    try:
        file_size, tcp_conns, udp_conns = get_user_input()
        server_ip, udp_port, tcp_port = listen_for_offers()
        start_speed_test(server_ip, tcp_port, udp_port, file_size, tcp_conns, udp_conns)
    except KeyboardInterrupt:
        print("Client shutting down.")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()
