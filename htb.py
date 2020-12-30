import random
import queue

PKT_MIN_LEN = 64
PKT_MAX_LEN = 1518
REPLENISH_INTERVAL = 0.001

HIGHEST_PRIO = 0
LOWEST_PRIO = 7

class TokenBucketNode(object):

    def __init__(self, name, rate, ceil, parent=None, debug=False):
        self.name = name
        # Token bucket parameters
        self.rate = rate
        self.ceil = ceil
        self.burst = rate
        self.cburst = ceil
        # Default r2q value : 10
        self.quantum = max(PKT_MAX_LEN, (rate / 10))
        self.parent = parent
        self.tokens = self.burst  # Current size of the bucket in bytes
        self.ctokens = self.cburst  # Current size of the bucket in bytes
        self.update_time = 0.0  # Last time the bucket was updated
        self.state = 'HTB_CAN_SEND'
        self.debug = debug

    def replenish(self, timestamp):
        """ Add tokens to bucket based on current time """
        now = timestamp

        if self.parent:
            self.parent.replenish(now)

        elapsed = now - self.update_time
        if self.debug:
            print("Elapsed: %f" % elapsed)
            print("Original self.tokens %f" % self.tokens)
            print("Original self.ctokens %f" % self.ctokens)
        self.tokens = min(self.burst, self.tokens + self.rate * elapsed)
        if self.debug:
            print("self.burst %f" % self.burst)
            print("self.rate * elapsed: %f" % (self.rate * elapsed))
            print("self.tokens %f" % self.tokens)
        self.ctokens = min(self.cburst, self.ctokens + self.ceil * elapsed)
        if self.debug:
            print("self.cburst %f" % self.cburst)
            print("self.crate * elapsed: %f" % (self.ceil * elapsed))
            print("self.ctokens %f" % self.ctokens)
        self.update_time = now
        self.update_state()

    def account(self, amount):
        if amount > self.tokens and amount > self.ctokens:
            if self.debug:
                print("%s: Exceeding all tokens" % (self.name))
            return False

        if self.parent:
            if not self.parent.account(amount):
                return False

        self.tokens = max(0, self.tokens - amount)
        self.ctokens = max(0, self.ctokens - amount)
        self.update_state()

        return True

    def borrow(self):
        """ Borrowing is allowed in quantum units if there is enough tokens.
        Otherwise, attempt to borrow from the parent. """

        if self.can_send():
            return True

        if self.can_borrow():
            return self.borrow_from_parent()

        return False

    def borrow_from_parent(self):
        """ Try and borrow tokens from the parent. """
        return self.parent and self.parent.borrow()

    def update_state(self):
        if self.tokens >= self.quantum:
            self.state = 'HTB_CAN_SEND'
        elif self.ctokens >= self.quantum:
            self.state = 'HTB_CAN_BORROW'
        else:
            self.state = 'HTB_CANNOT_SEND'

    def cannot_send(self):
        return self.state == 'HTB_CANNOT_SEND'

    def can_send(self):
        status = (self.state == 'HTB_CAN_SEND')
        if self.parent:
            status &= self.parent.can_send()
        return status

    def can_borrow(self):
        status = (self.state == 'HTB_CAN_BORROW')
        if self.parent:
            status &= (self.parent.can_send()
                       | self.parent.can_borrow())
        return status


class ShaperTokenBucket(TokenBucketNode):

    def __init__(self, env, name, rate, ceil, prio, parent=None, debug=False):
        super().__init__(name, rate, ceil, parent, debug)

        # Simulation variables
        self.env = env
        self.inp = None
        self.outp = None
        self.pkt_sent = self.env.event()
        self.q = None

        # Statistics
        self.packets_sent = 0
        self.bytes_sent = 0

        self.name = name
        self.debug = debug

        self.prio = prio

        self.action = env.process(self.run())

    def run(self):
        while True:
            if self.inp:
                self.q = self.inp.get_q()
                self.inp.enq_pkt()
                yield self.pkt_sent

    def send(self):
        while not self.q.empty():
            pkt = self.q.get()
            if self.account(pkt.size):
                self.outp.put(pkt)
                self.packets_sent += 1
                self.bytes_sent += pkt.size
            else:
                # TODO: how to put packet back to the head of queue
                self.q.put(pkt)
                break
        self.pkt_sent.succeed()
        self.pkt_sent = self.env.event()

    def borrow_and_send(self):
        if self.borrow_from_parent():
            self.send()
            return True
        return False

    def has_packets(self):
        if self.q is not None:
            return not self.q.empty()
        return False

    def stats(self, short=False):
        if short:
            return "%d" % (self.bytes_sent / self.env.now)
        else:
            return "%s: %d Bps" % (self.name, self.bytes_sent / self.env.now)


class RateLimiter(object):
    def __init__(self, env):
        self.shapers = []
        self.replenish_interval = REPLENISH_INTERVAL

        self.env = env
        self.action = env.process(self.run())

    def add_shaper(self, shaper):
        self.shapers.append(shaper)

    def replenish(self):
        now = self.env.now
        for shaper in self.shapers:
            shaper.replenish(now)

    def run(self):
        while True:
            self.replenish()

            self.process_nodes_that_can_send1()
            self.process_nodes_that_can_borrow1()

            yield self.env.timeout(self.replenish_interval)

    def process_nodes_that_can_send1(self):
        for prio_val in range(HIGHEST_PRIO, LOWEST_PRIO + 1):
            temp_shapers = []
            for shaper in self.shapers:
                if shaper.prio == prio_val:
                    temp_shapers.append(shaper)
            if temp_shapers:
                random.shuffle(temp_shapers)
                for shaper in temp_shapers:
                    while shaper.has_packets() and shaper.can_send():
                        shaper.send()

    def process_nodes_that_can_send(self):
        while True:
            sent = False
            random.shuffle(self.shapers)
            for shaper in self.shapers:
                if shaper.has_packets() and shaper.can_send():
                    shaper.send()
                    sent = True
            if not sent:
                break

    def process_nodes_that_can_borrow1(self):
        for prio_val in range(HIGHEST_PRIO, LOWEST_PRIO + 1):
            temp_shapers = []
            for shaper in self.shapers:
                if shaper.prio == prio_val:
                    temp_shapers.append(shaper)
            if temp_shapers:
                random.shuffle(temp_shapers)
                for shaper in temp_shapers:
                    while shaper.has_packets() and shaper.can_borrow():
                        shaper.borrow_and_send()

    def process_nodes_that_can_borrow(self):
        while True:
            sent = False
            random.shuffle(self.shapers)
            for shaper in self.shapers:
                if shaper.has_packets() and shaper.can_borrow():
                    sent |= shaper.borrow_and_send()
            if not sent:
                break


class Packet(object):
    def __init__(self, size):
        self.size = size


class PacketGenerator(object):
    def __init__(self, name, throughput):
        self.name = name
        self.packets_sent = 0
        self.bytes_sent = 0
        self.throughput = throughput
        self.q = queue.Queue()

    def get_pkt(self):
        pkt = Packet(random.randint(PKT_MIN_LEN,PKT_MAX_LEN))
        return pkt

    def enq_pkt(self):
        bytes_gen = 0
        while True:
            pkt = self.get_pkt()
            if bytes_gen + pkt.size >= self.throughput * REPLENISH_INTERVAL:
                break
            self.q.put(pkt)
            self.packets_sent += 1
            self.bytes_sent += pkt.size
            bytes_gen += pkt.size

    def get_q(self):
        return self.q



class PacketSink(object):
    def __init__(self, env, name):
        self.env = env
        self.name = name
        self.packets_recv = 0
        self.bytes_recv = 0
        self.last_arrival = 0.0

    def put(self, pkt):
        self.packets_recv += 1
        self.bytes_recv += pkt.size
        self.last_arrival = self.env.now

    def rate(self):
        return self.bytes_recv / self.last_arrival

    def stats(self):
        return "%s: %d Bps" % (self.name, self.rate())
