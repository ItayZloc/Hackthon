"""
Speed Test Client
=================
A multi-threaded client application that connects to a broadcasted server to
perform speed tests (TCP and UDP). The client has three main states:
  1) Startup
  2) Looking for a Server
  3) Speed Test

This version:
  - Prompts the user for the UDP broadcast port (default 13117).
  - Allows Ctrl+C to break out of the waiting loop.
  - Uses a short socket timeout to avoid indefinite blocking and eliminate busy waiting.
"""

import socket
import struct
import sys
import threading
import time

# --------------------- #
#   Global Constants    #
# --------------------- #

MAGIC_COOKIE = 0xabcddcba           # 4-byte magic cookie
OFFER_MESSAGE_TYPE = 0x2            # 1-byte for server's "offer" message
REQUEST_MESSAGE_TYPE = 0x3          # 1-byte for client's "request" message
PAYLOAD_MESSAGE_TYPE = 0x4          # 1-byte for server's data payload message

TCP_TIMEOUT = 5.0                   # Seconds before TCP connect times out
UDP_TIMEOUT = 5.0                   # Seconds of no data before UDP receiver stops

# ANSI color codes for nicer console output
COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_CYAN = "\033[96m"

# --------------------- #
#   Utility Functions   #
# --------------------- #

def color_print(message, color=COLOR_RESET):
    """Utility function to print colored text."""
    print(f"{color}{message}{COLOR_RESET}")


# --------------------- #
#   Worker Threads      #
# --------------------- #

class TCPWorker(threading.Thread):
    """
    A thread representing one TCP connection to the server.
    1) Connects to the server's TCP port.
    2) Sends a request packet: [magic_cookie (4 bytes), request_type (1 byte), file_size (8 bytes)].
    3) Receives the file, measures time, and calculates speed.
    """

    def __init__(self, thread_id, server_ip, server_tcp_port, file_size, results_dict):
        super().__init__()
        self.thread_id = thread_id
        self.server_ip = server_ip
        self.server_tcp_port = server_tcp_port
        self.file_size = file_size
        self.results_dict = results_dict

    def run(self):
        start_time = 0
        end_time = 0
        total_bytes_received = 0

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TCP_TIMEOUT)

            # Connect to the server's TCP port
            sock.connect((self.server_ip, self.server_tcp_port))

            # Send the request header: [magic_cookie, request_type, file_size]
            packet = struct.pack('!IBQ', MAGIC_COOKIE, REQUEST_MESSAGE_TYPE, self.file_size)
            sock.sendall(packet)

            start_time = time.time()

            # Receive the requested file
            received_bytes = b''
            while len(received_bytes) < self.file_size:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                received_bytes += chunk

            total_bytes_received = len(received_bytes)
            end_time = time.time()

            sock.close()

        except Exception as e:
            color_print(f"[TCP #{self.thread_id}] Error: {e}", COLOR_RED)
        finally:
            duration = 0
            speed_bps = 0
            if start_time and end_time and end_time > start_time:
                duration = end_time - start_time
                bits_received = total_bytes_received * 8
                speed_bps = bits_received / duration

            self.results_dict[self.thread_id] = {
                'bytes_received': total_bytes_received,
                'time': duration,
                'speed_bps': speed_bps,
            }
            color_print(
                f"[TCP #{self.thread_id}] Finished. Time={duration:.3f}s, Speed={speed_bps:.2f} bit/s",
                COLOR_GREEN
            )


class UDPWorker(threading.Thread):
    """
    A thread representing one UDP connection to the server.
    1) Sends a request packet: [magic_cookie (4 bytes), request_type (1 byte), file_size (8 bytes)].
    2) Receives multiple data packets:
       [magic_cookie (4 bytes), payload_type (1 byte), total_segments (8 bytes),
        current_segment (8 bytes), data (...)]
    3) Tracks all received segments (detects packet loss), measures time, and calculates throughput.
    """

    def __init__(self, thread_id, server_ip, server_udp_port, file_size, results_dict):
        super().__init__()
        self.thread_id = thread_id
        self.server_ip = server_ip
        self.server_udp_port = server_udp_port
        self.file_size = file_size
        self.results_dict = results_dict
        self.total_segments = None
        self.received_segments = None

    def run(self):
        start_time = 0
        end_time = 0
        total_bytes_received = 0
        packets_received_count = 0

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(UDP_TIMEOUT)

        try:
            # Send UDP request header: [magic_cookie, request_type, file_size]
            request_packet = struct.pack('!IBQ', MAGIC_COOKIE, REQUEST_MESSAGE_TYPE, self.file_size)
            sock.sendto(request_packet, (self.server_ip, self.server_udp_port))

            start_time = time.time()

            # Receive multiple payload segments until timeout or all segments arrive
            while True:
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    break

                # We expect 21 bytes for the header: [magic_cookie(4), payload_type(1), total_segments(8), current_segment(8)]
                if len(data) < 21:
                    continue

                (magic_cookie,
                 payload_type,
                 total_segments,
                 current_segment) = struct.unpack('!IBQQ', data[:21])

                if magic_cookie != MAGIC_COOKIE or payload_type != PAYLOAD_MESSAGE_TYPE:
                    continue  # ignore malformed packets

                payload_data = data[21:]
                total_bytes_received += len(payload_data)

                # Initialize if first packet
                if self.total_segments is None:
                    self.total_segments = total_segments
                    self.received_segments = [False] * total_segments

                # Mark segment received
                if 0 <= current_segment < total_segments:
                    if not self.received_segments[current_segment]:
                        self.received_segments[current_segment] = True
                        packets_received_count += 1

                # If all arrived, stop early
                if packets_received_count == total_segments:
                    break

            end_time = time.time()

        except Exception as e:
            color_print(f"[UDP #{self.thread_id}] Error: {e}", COLOR_RED)
        finally:
            duration = 0
            speed_bps = 0
            if start_time and end_time and end_time > start_time:
                duration = end_time - start_time
                bits_received = total_bytes_received * 8
                speed_bps = bits_received / duration

            total_segments = self.total_segments if self.total_segments else 0
            packets_received = packets_received_count
            packet_loss_percent = 0.0
            if total_segments > 0:
                packet_loss_percent = 100.0 * (total_segments - packets_received) / total_segments

            self.results_dict[self.thread_id] = {
                'bytes_received': total_bytes_received,
                'time': duration,
                'speed_bps': speed_bps,
                'total_segments': total_segments,
                'packets_received': packets_received,
                'packet_loss_percent': packet_loss_percent,
            }

            color_print(
                f"[UDP #{self.thread_id}] Finished. Time={duration:.3f}s, "
                f"Speed={speed_bps:.2f} bit/s, "
                f"Received {packets_received}/{total_segments} segments "
                f"({packet_loss_percent:.2f}% loss)",
                COLOR_CYAN
            )
            sock.close()


# --------------------- #
#      Main Client      #
# --------------------- #

def main():
    color_print("=== Speed Test Client ===", COLOR_GREEN)

    # Startup parameters
    color_print("Enter parameters (leave blank for defaults).", COLOR_YELLOW)

    try:
        # Prompt user for broadcast port
        broadcast_port_str = input("UDP broadcast port [default: 13117]: ")
        if not broadcast_port_str.strip():
            broadcast_port = 13117
        else:
            broadcast_port = int(broadcast_port_str)

        # Prompt user for file size in bytes
        file_size_str = input("File size (bytes) [default: 1000000]: ")
        if not file_size_str.strip():
            file_size = 1_000_000
        else:
            file_size = int(file_size_str)

        # Prompt user for how many TCP connections
        tcp_count_str = input("Number of TCP connections [default: 1]: ")
        if not tcp_count_str.strip():
            tcp_count = 1
        else:
            tcp_count = int(tcp_count_str)

        # Prompt user for how many UDP connections
        udp_count_str = input("Number of UDP connections [default: 1]: ")
        if not udp_count_str.strip():
            udp_count = 1
        else:
            udp_count = int(udp_count_str)

        color_print(
            f"Using broadcast_port={broadcast_port}, file_size={file_size}, "
            f"tcp_count={tcp_count}, udp_count={udp_count}\n",
            COLOR_YELLOW
        )
    except KeyboardInterrupt:
        color_print("\nUser requested exit.", COLOR_RED)
        sys.exit(0)
    except:
        color_print("\nInvalid input. Exiting.", COLOR_RED)
        sys.exit(1)

    # Create a UDP socket (non-blocking w/ short timeout) to listen for broadcast
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp_socket.settimeout(1.0)  # <-- short timeout to allow Ctrl+C to interrupt

        # For some systems:
        # udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        udp_socket.bind(("", broadcast_port))
    except Exception as e:
        color_print(f"Failed to create/bind UDP socket: {e}", COLOR_RED)
        sys.exit(1)

    color_print("Looking for a server... (Press Ctrl+C to quit)\n", COLOR_CYAN)

    while True:
        server_ip = None
        server_tcp_port = None
        server_udp_port = None

        try:
            # We'll wait for data (with a 1s timeout), so Ctrl+C can interrupt
            data, addr = udp_socket.recvfrom(1024)
        except socket.timeout:
            # No broadcast received yet, just loop
            continue
        except KeyboardInterrupt:
            color_print("\nUser requested exit.", COLOR_RED)
            break
        except Exception as e:
            color_print(f"Error receiving broadcast: {e}", COLOR_RED)
            continue

        # Minimal length check (4 + 1 + 2 + 2 = 9 bytes)
        if len(data) < 7:
            continue

        magic_cookie, message_type = struct.unpack('!IB', data[:5])
        if magic_cookie != MAGIC_COOKIE or message_type != OFFER_MESSAGE_TYPE:
            continue

        offset = 5
        if len(data) >= offset + 2:
            server_tcp_port = struct.unpack('!H', data[offset:offset+2])[0]
            offset += 2
        if len(data) >= offset + 2:
            server_udp_port = struct.unpack('!H', data[offset:offset+2])[0]
            offset += 2

        server_ip = addr[0]

        color_print(
            f"Received offer from {server_ip}. "
            f"TCP port={server_tcp_port}, UDP port={server_udp_port}",
            COLOR_GREEN
        )

        if server_ip and server_tcp_port and server_udp_port:
            # Transition to Speed Test
            speed_test(server_ip, server_tcp_port, server_udp_port, file_size, tcp_count, udp_count)
            # After speed test, user can choose to keep looking or exit
            cont = input("Press Enter to look for another server, or type 'q' to quit: ")
            if cont.strip().lower() == 'q':
                color_print("Exiting client.", COLOR_RED)
                break

    udp_socket.close()


def speed_test(server_ip, server_tcp_port, server_udp_port, file_size, tcp_count, udp_count):
    """
    Speed Test phase:
      1) Spawn threads for TCP and UDP.
      2) Wait for all threads to complete.
      3) Print summary.
    """
    color_print("\n=== Speed Test Phase ===", COLOR_YELLOW)
    color_print(f"Connecting to {server_ip} (TCP={server_tcp_port}, UDP={server_udp_port})\n", COLOR_YELLOW)

    tcp_results = {}
    udp_results = {}

    # Launch TCP threads
    tcp_threads = []
    for i in range(tcp_count):
        t = TCPWorker(
            thread_id=i+1,
            server_ip=server_ip,
            server_tcp_port=server_tcp_port,
            file_size=file_size,
            results_dict=tcp_results
        )
        tcp_threads.append(t)
        t.start()

    # Launch UDP threads
    udp_threads = []
    for i in range(udp_count):
        t = UDPWorker(
            thread_id=i+1,
            server_ip=server_ip,
            server_udp_port=server_udp_port,
            file_size=file_size,
            results_dict=udp_results
        )
        udp_threads.append(t)
        t.start()

    # Wait for threads to finish
    for t in tcp_threads:
        t.join()
    for t in udp_threads:
        t.join()

    # Print summary
    color_print("\n--- TCP Summary ---", COLOR_GREEN)
    for thread_id, res in sorted(tcp_results.items()):
        color_print(
            f"TCP #{thread_id}: Bytes Received={res['bytes_received']}, "
            f"Time={res['time']:.3f}s, Speed={res['speed_bps']:.2f} bit/s"
        )

    color_print("\n--- UDP Summary ---", COLOR_CYAN)
    for thread_id, res in sorted(udp_results.items()):
        color_print(
            f"UDP #{thread_id}: Bytes Received={res['bytes_received']}, "
            f"Time={res['time']:.3f}s, Speed={res['speed_bps']:.2f} bit/s, "
            f"Packet Loss={res['packet_loss_percent']:.2f}% "
            f"({res.get('packets_received',0)}/{res.get('total_segments',0)})"
        )


if __name__ == "__main__":
    main()
