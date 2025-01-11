#!/usr/bin/env python3
"""
Speed Test Client (Dynamic Port + Error Handling)
=================================================
A multi-threaded client application that connects to a broadcasted server to
perform speed tests (TCP and UDP). The client has three main states:
  1) Startup
  2) Looking for a Server
  3) Speed Test

Changes in this version:
1) The user can choose the UDP broadcast port at runtime (defaults to 13117 if blank).
2) Explicit error handling for WinError 10061 (Connection Refused) and WinError 10054
   (Connection Reset by Peer) in the worker threads.
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
UDP_TIMEOUT = 1.0                   # Seconds of no data before UDP receiver stops

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
        """
        :param thread_id: An integer identifying this thread (e.g., #1).
        :param server_ip: String IP address of the server.
        :param server_tcp_port: Integer TCP port of the server.
        :param file_size: The requested file size in bytes.
        :param results_dict: A dictionary to store the result metrics (time, speed).
        """
        super().__init__()
        self.thread_id = thread_id
        self.server_ip = server_ip
        self.server_tcp_port = server_tcp_port
        self.file_size = file_size
        self.results_dict = results_dict

    def run(self):
        start_time = 0.0
        end_time = 0.0
        total_bytes_received = 0

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(TCP_TIMEOUT)
            try:
                # Attempt to connect
                sock.connect((self.server_ip, self.server_tcp_port))
            except socket.error as e:
                # Check for WinError 10061 (Connection Refused), 10054 (Connection Reset), etc.
                if hasattr(e, 'winerror'):
                    if e.winerror == 10061:
                        color_print(
                            f"[TCP #{self.thread_id}] Connection refused (WinError 10061). "
                            f"Server not listening or firewall blocking.",
                            COLOR_RED
                        )
                    elif e.winerror == 10054:
                        color_print(
                            f"[TCP #{self.thread_id}] Connection reset by peer (WinError 10054). "
                            f"Server forcibly closed or network dropped.",
                            COLOR_RED
                        )
                    else:
                        color_print(
                            f"[TCP #{self.thread_id}] Socket error (WinError {e.winerror}): {str(e)}",
                            COLOR_RED
                        )
                else:
                    color_print(f"[TCP #{self.thread_id}] Socket error: {str(e)}", COLOR_RED)
                return  # Cannot proceed with transfer if we failed to connect

            # Build and send the request packet
            # [magic_cookie (4 bytes), request_type (1 byte), file_size (8 bytes)]
            packet = struct.pack('!IBQ', MAGIC_COOKIE, REQUEST_MESSAGE_TYPE, self.file_size)
            sock.sendall(packet)

            # Measure start time
            start_time = time.time()

            # Receive data until we've gotten file_size bytes or the server stops
            received_bytes = b''
            while len(received_bytes) < self.file_size:
                try:
                    chunk = sock.recv(4096)
                except socket.error as e:
                    if hasattr(e, 'winerror') and e.winerror == 10054:
                        color_print(
                            f"[TCP #{self.thread_id}] Connection reset by peer (WinError 10054) "
                            f"during recv().",
                            COLOR_RED
                        )
                    else:
                        color_print(
                            f"[TCP #{self.thread_id}] Socket error during recv(): {str(e)}",
                            COLOR_RED
                        )
                    break  # Stop receiving
                if not chunk:
                    # Server closed the connection or no more data
                    break
                received_bytes += chunk

            total_bytes_received = len(received_bytes)
            end_time = time.time()

            # Close socket
            sock.close()

        except Exception as ex:
            color_print(f"[TCP #{self.thread_id}] Unexpected exception: {ex}", COLOR_RED)
        finally:
            if start_time and end_time and end_time > start_time:
                duration = end_time - start_time
                bits_received = total_bytes_received * 8
                speed_bps = bits_received / duration
            else:
                duration = 0
                speed_bps = 0

            # Save results
            self.results_dict[self.thread_id] = {
                'bytes_received': total_bytes_received,
                'time': duration,
                'speed_bps': speed_bps,
            }

            color_print(
                f"[TCP #{self.thread_id}] Done. Bytes={total_bytes_received}, "
                f"Time={duration:.3f}s, Speed={speed_bps:.2f} bps",
                COLOR_GREEN
            )


class UDPWorker(threading.Thread):
    """
    A thread representing one UDP connection to the server.
    1) Sends a request packet: [magic_cookie (4 bytes), request_type (1 byte), file_size (8 bytes)].
    2) Receives multiple data packets: 
       [magic_cookie (4 bytes), payload_type (1 byte), total_segments (4 bytes),
        current_segment (4 bytes), data (...)]
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
        start_time = 0.0
        end_time = 0.0
        total_bytes_received = 0
        packets_received_count = 0

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(UDP_TIMEOUT)

        try:
            # Send initial request to server
            request_packet = struct.pack('!IBQ', MAGIC_COOKIE, REQUEST_MESSAGE_TYPE, self.file_size)
            sock.sendto(request_packet, (self.server_ip, self.server_udp_port))

            # Start timing
            start_time = time.time()

            # Keep receiving data until timeout or all segments are received
            while True:
                try:
                    data, _ = sock.recvfrom(65535)
                except socket.timeout:
                    # No data for UDP_TIMEOUT seconds, break
                    break
                except socket.error as e:
                    # For UDP, 10054 can also appear if the server forcibly closes or resets something
                    # but it's less common with connectionless protocols. We'll log it anyway.
                    if hasattr(e, 'winerror'):
                        if e.winerror == 10054:
                            color_print(
                                f"[UDP #{self.thread_id}] Connection reset (WinError 10054). "
                                f"Remote might have dropped packets or firewall interfered.",
                                COLOR_RED
                            )
                        elif e.winerror == 10061:
                            color_print(
                                f"[UDP #{self.thread_id}] Connection refused (WinError 10061). "
                                f"Server not reachable on that UDP port?",
                                COLOR_RED
                            )
                        else:
                            color_print(
                                f"[UDP #{self.thread_id}] Socket error (WinError {e.winerror}): {str(e)}",
                                COLOR_RED
                            )
                    else:
                        color_print(f"[UDP #{self.thread_id}] Socket error: {str(e)}", COLOR_RED)
                    break

                # Parse incoming data
                # [magic_cookie (4 bytes), payload_type (1 byte), total_segments (4 bytes),
                #  current_segment (4 bytes), payload...]
                if len(data) < 13:
                    continue

                (magic_cookie,
                 payload_type,
                 total_segments,
                 current_segment) = struct.unpack('!IBII', data[:13])

                # Validate magic cookie and payload type
                if magic_cookie != MAGIC_COOKIE or payload_type != PAYLOAD_MESSAGE_TYPE:
                    continue

                payload_data = data[13:]
                total_bytes_received += len(payload_data)

                # Initialize segment tracking if needed
                if self.total_segments is None:
                    self.total_segments = total_segments
                    self.received_segments = [False] * total_segments

                # Mark the current segment as received
                if 0 <= current_segment < total_segments:
                    if not self.received_segments[current_segment]:
                        self.received_segments[current_segment] = True
                        packets_received_count += 1

                # If we received all segments, we can stop early
                if packets_received_count == total_segments:
                    break

            end_time = time.time()

        except Exception as ex:
            color_print(f"[UDP #{self.thread_id}] Unexpected exception: {ex}", COLOR_RED)
        finally:
            sock.close()

            if start_time and end_time and end_time > start_time:
                duration = end_time - start_time
                bits_received = total_bytes_received * 8
                speed_bps = bits_received / duration
            else:
                duration = 0
                speed_bps = 0

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
                f"[UDP #{self.thread_id}] Done. Time={duration:.3f}s, "
                f"Speed={speed_bps:.2f} bps, Received={packets_received}/{total_segments} "
                f"({packet_loss_percent:.2f}% loss)",
                COLOR_CYAN
            )


# --------------------- #
#      Main Client      #
# --------------------- #

def main():
    color_print("=== Speed Test Client (Dynamic Port + WinError Handling) ===", COLOR_GREEN)

    # --------------------- #
    #         Startup       #
    # --------------------- #

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

    # Create a UDP socket to listen for broadcast offers on the chosen port
    try:
        udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # On some OSes, you may also need:
        # udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        # Bind to user-chosen port for receiving broadcast
        udp_socket.bind(("", broadcast_port))
    except Exception as e:
        color_print(f"Failed to create/bind UDP socket: {e}", COLOR_RED)
        sys.exit(1)

    # --------------------- #
    #   Looking for Server  #
    # --------------------- #

    while True:
        color_print("Looking for a server... (Press Ctrl+C to quit)", COLOR_CYAN)
        server_ip = None
        server_tcp_port = None
        server_udp_port = None

        try:
            # We'll block waiting for data from any server broadcasting an offer
            data, addr = udp_socket.recvfrom(1024)
            # data expected: [magic_cookie (4 bytes), message_type (1 byte), ... port info...]

            # Minimal length check (4 + 1 + at least 2 for TCP port)
            if len(data) < 7:
                continue

            magic_cookie, message_type = struct.unpack('!IB', data[:5])

            if magic_cookie != MAGIC_COOKIE or message_type != OFFER_MESSAGE_TYPE:
                # Ignore malformed or incorrect offers
                continue

            # Suppose the server next sends (2 bytes for TCP port, 2 bytes for UDP port).
            # Adjust based on your actual protocol.
            offset = 5
            if len(data) >= offset + 2:
                server_tcp_port = struct.unpack('!H', data[offset:offset+2])[0]
                offset += 2
            if len(data) >= offset + 2:
                server_udp_port = struct.unpack('!H', data[offset:offset+2])[0]
                offset += 2

            # The server IP is the source IP from the broadcast
            server_ip = addr[0]

            color_print(
                f"Received offer from {server_ip}. TCP port={server_tcp_port}, UDP port={server_udp_port}",
                COLOR_GREEN
            )

        except KeyboardInterrupt:
            color_print("\nUser requested exit.", COLOR_RED)
            break
        except Exception as e:
            color_print(f"Error receiving broadcast: {e}", COLOR_RED)
            continue

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

    # Prepare shared dictionaries for TCP/UDP results
    tcp_results = {}
    udp_results = {}

    # --------------------- #
    #   Launch TCP threads  #
    # --------------------- #
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

    # --------------------- #
    #   Launch UDP threads  #
    # --------------------- #
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

    # Wait for all threads to finish
    for t in tcp_threads:
        t.join()
    for t in udp_threads:
        t.join()

    # --- Print Summaries ---
    color_print("\n--- TCP Summary ---", COLOR_GREEN)
    for thread_id, res in sorted(tcp_results.items()):
        color_print(
            f"TCP #{thread_id}: Bytes Received={res['bytes_received']}, "
            f"Time={res['time']:.3f}s, Speed={res['speed_bps']:.2f} bps"
        )

    color_print("\n--- UDP Summary ---", COLOR_CYAN)
    for thread_id, res in sorted(udp_results.items()):
        color_print(
            f"UDP #{thread_id}: Bytes Received={res['bytes_received']}, "
            f"Time={res['time']:.3f}s, Speed={res['speed_bps']:.2f} bps, "
            f"Packet Loss={res['packet_loss_percent']:.2f}% "
            f"({res.get('packets_received',0)}/{res.get('total_segments',0)})"
        )


if __name__ == "__main__":
    main()
