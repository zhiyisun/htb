"""
This file shows an example of a traffic shaper whose bucket size is
the same of the packet size and whose bucket rate is one half the input
packet rate.
In addition it shows a method of plotting packet arrival and exit times.
Copyright Dr. Greg M. Bernstein 2014
Released under the MIT license
"""
import simpy
import htb
from graphviz import Digraph

NAME = 0
RATE = 1
CEIL = 2
CHILDREN = 3

MAX_THROUGHPUT = 30000000
SIMPY_ITERATION = 1000

def create_leaf_node(env, node, parent):
    return htb.ShaperTokenBucket(
        env, node[NAME], node[RATE], node[CEIL], parent)


def create_inner_node(node, parent):
    return htb.TokenBucketNode(
        node[NAME], node[RATE], node[CEIL], parent)


def create_shaper_subtree(env, nodes, parent):
    shapers = []
    for node in nodes:
        if len(node[CHILDREN]) == 0:
            shapers.append(create_leaf_node(env, node, parent))
        else:
            inner_node = create_inner_node(node, parent)
            shapers += create_shaper_subtree(
                env, node[CHILDREN], inner_node)
    return shapers


def create_rate_limiter(env, profile, source, sink):
    shapers = []
    rl = htb.RateLimiter(env)

    root = create_inner_node(profile, None)
    shapers += create_shaper_subtree(env, profile[CHILDREN], root)

    for shaper in shapers:
        shaper.inp = source
        shaper.outp = sink
        rl.add_shaper(shaper)

    return rl

def progress_bar(env):
    while True:
        print("#", end="", flush=True)
        yield env.timeout(1)

def simulate(name, profile):
    env = simpy.Environment()

    pg = htb.PacketGenerator("Source", MAX_THROUGHPUT)
    ps = htb.PacketSink(env, 'Sink')

    rl = create_rate_limiter(env, profile, pg, ps)

    env.process(progress_bar(env))

    env.run(until=SIMPY_ITERATION)

    print('[' + name + ']')
    rl.shapers.sort(key=lambda x: x.name)
    for shaper in rl.shapers:
        print(shaper.stats())
    print(ps.stats())
    print()

    render(name, rl.shapers)


def render(profile, shapers):
    def format_label(name, ceil, rate):
        return "%s|{C:%d|R:%d}" % (name, ceil, rate)

    g = Digraph(format='png', strict=True)
    g.body.extend(['rankdir=BT'])
    g.attr('node', shape='record', style='rounded')

    inner_nodes = []
    for shaper in shapers:
        g.node(shaper.name,
               format_label(shaper.name, shaper.ceil, shaper.rate))

        parent = shaper.parent
        child = shaper
        edge_label = shaper.stats(short=True)
        while parent:
            if parent.name not in inner_nodes:
                inner_nodes.append(parent.name)
                g.node(parent.name,
                       format_label(parent.name, parent.ceil, parent.rate))
                g.edge(parent.name, child.name, edge_label)
                break

            g.edge(parent.name, child.name, edge_label)
            edge_label = ''
            child = parent
            parent = child.parent

    g.body.append('{ rank=same %s }' % (' '.join([x.name for x in shapers])))
    g.render('images/'+profile)


if __name__ == '__main__':
    profile = ('Root', 25000000, 25000000,
               [('S1', 2000000, 5000000, []),
                ('S2', 6000000, 10000000, []),
                ('S3', 3000000, 7000000, []),
                ('S4', 4000000, 6000000, [])])
    simulate("Profile", profile)
