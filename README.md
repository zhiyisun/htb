A Hierarchical Token Bucket (HTB) model implementation using simpy.

Forked from https://github.com/stevedoyle/htb

Enhancement:

1.  Enhance packet generator to provide a queue to shaper. And fill random size (64-1518) packets to the queue. Shaper will get packets form this queue
2.  Smaller replenish interval to get higher accuracy
3.  Packet generator per queue

Todo:

1.  Shaper has to dequeue the packet to know the length of packet. But if packet length is larger than available token, this packet can't be scheduled. So this code just enqueue this packet to the queue again.
