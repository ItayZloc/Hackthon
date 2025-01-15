# Speed Test Client and Server

A project designed to test network speed and reliability between a client and server using TCP and UDP connections. The project is implemented in Python and supports multi-threaded communication for efficiency and scalability.

### Server
- Dynamically assigns TCP and UDP ports for communication.
- Broadcasts its presence over UDP for client discovery.
- Handles TCP and UDP client requests in parallel using multi-threading.
- Provides data streams over TCP and UDP for speed testing.

### Client
- Automatically discovers the server through UDP broadcasts.
- Supports multi-threaded TCP and UDP speed tests.
- Measures metrics such as throughput, latency, and packet loss.
- Customizable parameters for testing (e.g., file size, number of connections).

## Requirements

- Python 3.7 or later
- A network environment that supports UDP broadcasting
