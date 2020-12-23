A Hierarchical Token Bucket (HTB) model implementation using simpy.

Forked from https://github.com/stevedoyle/htb

Enhancement:

1.  Enhance packet generator to provide a queue to shaper. And fill random size (64-1518) packets to the queue. Shaper will get packets form this queue
2.  Smaller replenish interval to get higher accuracy

Todo:

1.  Single packet generator as of now. All shaper will get packet from this packet generator. That means to test the behavior of this HTB model, it has to generate traffic larnger than the interface throughput, or max rate of root node. But it can't specify the traffic of each class. So this model can't test if some classes has less than CIR traffic.
2.  Shaper has to dequeue the packet to know the length of packet. But if packet length is larger than available token, this packet can't be scheduled. So this code just enqueue this packet to the queue again.
