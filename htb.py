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

    def account_cir(self, amount):
        if amount > self.tokens:
            if self.debug:
                print("%s: Exceeding all tokens" % (self.name))
            return False

        if self.parent:
            if not self.parent.account_cir(amount):
                return False

        self.tokens = max(0, self.tokens - amount)
        self.ctokens = max(0, self.ctokens - amount)
        self.update_state()

        return True

    def account_pir(self, amount):
        if amount > self.tokens and amount > self.ctokens:
            if self.debug:
                print("%s: Exceeding all tokens" % (self.name))
            return False

        if self.parent:
            if not self.parent.account_pir(amount):
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

    def __init__(self, env, name, rate, ceil, prio, input_rate, parent=None, debug=False):
        super().__init__(name, rate, ceil, parent, debug)

        # Simulation variables
        self.env = env
        self.inp = PacketGenerator(env, "Source_" + name, input_rate)
        self.outp = PacketSink(env, 'Sink_'+ name)

        # Statistics
        self.packets_sent = 0
        self.bytes_sent = 0
        self.last_sent_time = 0

        self.name = name
        self.debug = debug

        self.prio = prio
        self.input_rate = input_rate

        self.action = env.process(self.run())

    def run(self):
        while True:
            if self.inp:
                self.inp.enq_pkt()
            yield self.env.timeout(REPLENISH_INTERVAL)

    def send_cir(self):
        while not self.inp.q.empty():
            pkt = self.inp.q.get(block=False)
            if self.account_cir(pkt.size):
                self.outp.put(pkt)
                self.packets_sent += 1
                self.bytes_sent += pkt.size
                self.last_sent_time = self.env.now
            else:
                # TODO: how to put packet back to the head of queue
                self.inp.q.put(pkt)
                break

    def send_pir(self):
        while not self.inp.q.empty():
            pkt = self.inp.q.get(block=False)
            if self.account_pir(pkt.size):
                self.outp.put(pkt)
                self.packets_sent += 1
                self.bytes_sent += pkt.size
                self.last_sent_time = self.env.now
            else:
                # TODO: how to put packet back to the head of queue
                self.inp.q.put(pkt)
                break


    def borrow_and_send(self):
        if self.borrow_from_parent():
            self.send_pir()
            return True
        return False

    def has_packets(self):
        if self.inp.q is not None:
            return not self.inp.q.empty()
        return False

    def stats(self, short=False):
        if short:
            return "%d %d" % (self.bytes_sent, self.bytes_sent / self.env.now)
        else:
            return "%s sent: %d packets(%d B) rate: %d Bps" % (self.name, self.packets_sent, self.bytes_sent, self.bytes_sent / self.last_sent_time)


class RateLimiter(object):
    def __init__(self, env):
        self.shapers = []
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

            self.process_nodes_that_can_send()
            self.process_nodes_that_can_borrow()

            yield self.env.timeout(REPLENISH_INTERVAL)

    def process_nodes_that_can_send(self):
        for prio_val in range(HIGHEST_PRIO, LOWEST_PRIO + 1):
            temp_shapers = []
            for shaper in self.shapers:
                if shaper.prio == prio_val:
                    temp_shapers.append(shaper)
            if temp_shapers:
                random.shuffle(temp_shapers)
                for shaper in temp_shapers:
                    while shaper.has_packets() and shaper.can_send():
                        shaper.send_cir()

    def process_nodes_that_can_borrow(self):
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

class Packet(object):
    def __init__(self, size):
        self.size = size


class PacketGenerator(object):
    def __init__(self, env, name, throughput):
        self.name = name
        self.packets_sent = 0
        self.bytes_sent = 0
        self.throughput = throughput
        self.q = queue.Queue()
        self.last_sent = 0.0
        self.env = env

    def get_pkt(self):
        pkt = Packet(random.randint(PKT_MIN_LEN,PKT_MAX_LEN))
        return pkt

    def enq_pkt(self):
        bytes_gen = 0
        while True:
            pkt = self.get_pkt()
            if (self.env.now == 0):
                if (bytes_gen + pkt.size > self.throughput * REPLENISH_INTERVAL):
                    break
            else:
                if (((self.bytes_sent + pkt.size)/self.env.now) > self.throughput):
                    break
            self.q.put(pkt)
            self.packets_sent += 1
            self.bytes_sent += pkt.size
            bytes_gen += pkt.size
        self.last_sent = self.env.now

    def rate(self):
        if (self.last_sent != 0):
            return self.bytes_sent / self.last_sent
        else:
            return 0

    def stats(self):
        return "%s sent: %d packets(%d B) rate: %d Bps" % (self.name, self.packets_sent, self.bytes_sent, self.rate())


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
        if (self.last_arrival != 0):
            return self.bytes_recv / self.last_arrival
        else:
            return 0

    def stats(self):
        return "%s sent: %d packets(%d B) rate: %d Bps" % (self.name, self.packets_recv, self.bytes_recv, self.rate())
