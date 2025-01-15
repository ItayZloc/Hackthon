import socket
import struct
import threading
import time

# --------------------- #
#   ANSI Color Codes    #
# --------------------- #

COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_CYAN = "\033[96m"



def color_print(message, color=COLOR_RESET):
    """Utility function to print colored text."""
    print(f"{color}{message}{COLOR_RESET}")


# --------------------- #
#   Global Constants    #
# --------------------- #

MAGIC_COOKIE = 0xabcddcba           # Magic number to verify incoming requests
MSG_TYPE_OFFER = 0x02               # Message type for broadcast offers
MSG_TYPE_REQUEST = 0x03             # Message type for client requests
MSG_TYPE_PAYLOAD = 0x04             # Message type for data payload
UDP_PAYLOAD_CHUNK_SIZE = 1024       # Maximum size of each UDP payload chunk in bytes
TCP_SEND_CHUNK_SIZE = 4096          # Maximum size of each TCP payload chunk in bytes


# --------------------- #
#   Class-Based Server  #
# --------------------- #

class UDPServer:
    """
    Handles UDP communication with clients. It listens for requests and sends
    data in response.
    """
    def __init__(self, udp_port, stop_event):
        """
        Initializes the UDP server.

        :param udp_port: The port to bind the UDP socket to.
        :param stop_event: Event to signal when the server should stop.
        """
        self.udp_port = udp_port
        self.stop_event = stop_event
        self.socket = None

    def start(self):
        """Starts the UDP server and listens for incoming requests."""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(("0.0.0.0", self.udp_port))
        color_print(f"[UDP] Listening for requests on port {self.udp_port}...", COLOR_YELLOW)

        try:
            while not self.stop_event.is_set():
                # Receive data and the address of the client
                data, client_addr = self.socket.recvfrom(2048)
                # Handle the request in a separate thread
                threading.Thread(
                    target=self._handle_request,
                    args=(data, client_addr),
                    daemon=True
                ).start()
        except OSError:
            color_print("[UDP] Socket closed, shutting down UDP listener.", COLOR_YELLOW)
        finally:
            self.socket.close()

    def _handle_request(self, data, client_addr):
        """
        Handles a single UDP request.

        :param data: The data received from the client.
        :param client_addr: The address of the client.
        """
        if len(data) < 13:
            return  # Ignore malformed requests

        # Parse the request header
        magic_cookie, msg_type, file_size = struct.unpack("!IBQ", data)
        if magic_cookie != MAGIC_COOKIE or msg_type != MSG_TYPE_REQUEST:
            return  # Invalid request

        color_print(f"[UDP] Got request from {client_addr}, file_size={file_size} bytes", COLOR_CYAN)

        # Send the requested data in chunks
        num_segments = (file_size + UDP_PAYLOAD_CHUNK_SIZE - 1) // UDP_PAYLOAD_CHUNK_SIZE
        bytes_left = file_size
        for segment_index in range(num_segments):
            header = struct.pack("!IBQQ",
                                 MAGIC_COOKIE,
                                 MSG_TYPE_PAYLOAD,
                                 num_segments,
                                 segment_index)
            chunk_size = min(UDP_PAYLOAD_CHUNK_SIZE, bytes_left)
            payload_data = b'\x00' * chunk_size  # Dummy data
            packet = header + payload_data
            try:
                self.socket.sendto(packet, client_addr)
            except Exception as e:
                color_print(f"[UDP] Error sending segment {segment_index}: {e}", COLOR_RED)
                break
            bytes_left -= chunk_size
            if bytes_left <= 0:
                break

        color_print(f"[UDP] Finished sending {file_size} bytes to {client_addr}", COLOR_CYAN)


class TCPServer:
    """
    Handles TCP communication with clients. It listens for connections and sends
    data in response.
    """
    def __init__(self, tcp_port, stop_event):
        """
        Initializes the TCP server.

        :param tcp_port: The port to bind the TCP socket to.
        :param stop_event: Event to signal when the server should stop.
        """
        self.tcp_port = tcp_port
        self.stop_event = stop_event
        self.socket = None

    def start(self):
        """Starts the TCP server and listens for incoming connections."""
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.bind(("0.0.0.0", self.tcp_port))
        self.socket.listen(5)
        color_print(f"[TCP] Listening for connections on port {self.tcp_port}...", COLOR_YELLOW)

        try:
            while not self.stop_event.is_set():
                # Accept a client connection
                client_sock, client_addr = self.socket.accept()
                # Handle the connection in a separate thread
                threading.Thread(
                    target=self._handle_connection,
                    args=(client_sock, client_addr),
                    daemon=True
                ).start()
        except OSError:
            color_print("[TCP] Socket closed, shutting down TCP listener.", COLOR_YELLOW)
        finally:
            self.socket.close()

    def _handle_connection(self, client_sock, client_addr):
        """
        Handles a single TCP connection.

        :param client_sock: The socket for the connected client.
        :param client_addr: The address of the connected client.
        """
        color_print(f"[TCP] Accepted connection from {client_addr}", COLOR_GREEN)
        try:
            # Receive the 13-byte header
            header = client_sock.recv(13)
            if len(header) < 13:
                color_print(f"[TCP] Incomplete request header from {client_addr}", COLOR_RED)
                return

            # Parse the request header
            magic_cookie, req_type, file_size = struct.unpack('!IBQ', header)
            if magic_cookie != MAGIC_COOKIE or req_type != MSG_TYPE_REQUEST:
                color_print(f"[TCP] Invalid request header from {client_addr}", COLOR_RED)
                return

            color_print(f"[TCP] Client requests {file_size} bytes", COLOR_GREEN)

            # Send the requested data in chunks
            bytes_left = file_size
            while bytes_left > 0:
                chunk_size = min(TCP_SEND_CHUNK_SIZE, bytes_left)
                data_to_send = b'\x00' * chunk_size  # Dummy data
                client_sock.sendall(data_to_send)
                bytes_left -= chunk_size

            color_print(f"[TCP] Finished sending {file_size} bytes to {client_addr}", COLOR_GREEN)

        except Exception as e:
            color_print(f"[TCP] Error during transfer: {e}", COLOR_RED)
        finally:
            client_sock.close()
            color_print(f"[TCP] Connection closed with {client_addr}", COLOR_YELLOW)


class BroadcastServer:
    """
    Handles broadcasting the server's presence to clients over UDP.
    """
    def __init__(self, broadcast_port, tcp_port, udp_port, stop_event):
        """
        Initializes the broadcast server.

        :param broadcast_port: The port to broadcast offers on.
        :param tcp_port: The TCP port the server listens on.
        :param udp_port: The UDP port the server listens on.
        :param stop_event: Event to signal when the server should stop.
        """
        self.broadcast_port = broadcast_port
        self.tcp_port = tcp_port
        self.udp_port = udp_port
        self.stop_event = stop_event

    def start(self):
        """Starts broadcasting offers."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            color_print(f"[Broadcast] Broadcasting on port {self.broadcast_port}...", COLOR_YELLOW)
            while not self.stop_event.is_set():
                try:
                    # Broadcast the server's ports
                    offer_msg = struct.pack("!IBHH",
                                            MAGIC_COOKIE,
                                            MSG_TYPE_OFFER,
                                            self.tcp_port,
                                            self.udp_port)
                    sock.sendto(offer_msg, ("255.255.255.255", self.broadcast_port))
                except Exception as e:
                    color_print(f"[Broadcast] Error: {e}", COLOR_RED)
                self.stop_event.wait(timeout=1.0)


class Server:
    """
    The main server class that initializes and runs the UDP, TCP, and Broadcast servers.
    """
    def __init__(self, broadcast_port):
        """
        Initializes the server with the specified broadcast port.

        :param broadcast_port: The port to broadcast offers on.
        """
        self.broadcast_port = broadcast_port
        self.tcp_port = 0
        self.udp_port = 0
        self.stop_event = threading.Event()

    def run(self):
        """Runs the server."""
        # Dynamically assign TCP and UDP ports
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_sock.bind(('', 0))
        self.tcp_port = tcp_sock.getsockname()[1]
        tcp_sock.close()

        udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp_sock.bind(('', 0))
        self.udp_port = udp_sock.getsockname()[1]
        udp_sock.close()

        color_print(f"[Server] Dynamic ports assigned: TCP={self.tcp_port}, UDP={self.udp_port}", COLOR_CYAN)

        # Start the servers
        broadcast_server = BroadcastServer(self.broadcast_port, self.tcp_port, self.udp_port, self.stop_event)
        udp_server = UDPServer(self.udp_port, self.stop_event)
        tcp_server = TCPServer(self.tcp_port, self.stop_event)

        threading.Thread(target=broadcast_server.start, daemon=True).start()
        threading.Thread(target=udp_server.start, daemon=True).start()
        threading.Thread(target=tcp_server.start, daemon=True).start()

        # Keep the server running until interrupted
        color_print("[Server] Running. Press Ctrl+C to stop.", COLOR_CYAN)
        try:
            while True:
                time.sleep(1.0)
        except KeyboardInterrupt:
            color_print("[Server] Stopping...", COLOR_RED)
            self.stop_event.set()


if __name__ == "__main__":
    # Prompt user for broadcast port
    default_broadcast_port = 13117
    try:
        user_input = input(f"Enter broadcast port [default: {default_broadcast_port}]: ").strip()
        broadcast_port = int(user_input) if user_input else default_broadcast_port
    except ValueError:
        color_print("Invalid input. Using default broadcast port.", COLOR_RED)
        broadcast_port = default_broadcast_port

    # Start the server
    server = Server(broadcast_port)
    server.run()
