import re
import nltk
import numpy as np
import networkx as nx
import graphviz

from tqdm import tqdm
from collections import defaultdict, Counter
from functools import cached_property
from itertools import product

from leftcorner.semiring import Semiring, Boolean
from leftcorner.misc import colors, format_table


def _gen_nt(prefix=''):
    _gen_nt.i += 1
    return f'{prefix}@{_gen_nt.i}'
_gen_nt.i = 0


# make this a method on Chart, and suppress zeros in the Chart's repr
def keep_nonzero(semiring, chart):
    new = semiring.chart()
    for k, v in chart.items():
        if v == semiring.zero: continue
        new[k] = v
    return new


# TODO: make this what Semiring.chart returns
class Chart(dict):

    def _repr_html_(self):
        return ('<div style="font-family: Monospace;">'
                + format_table(self.items(), headings=['item', 'value'])
                + '</div>')


class Slash:

    def __init__(self, Y, Z, id):
        self.Y, self.Z = Y, Z
        self._hash = hash((Y, Z, id))
        self.id = id

    def __repr__(self):
        if self.id == 0:
            return f'{self.Y}/{self.Z}'
        else:
            return f'{self.Y}/{self.Z}@{self.id}'

    def __hash__(self):
        return self._hash

    def __eq__(self, other):
        return (
            isinstance(other, Slash)
            and self.Y == other.Y
            and self.Z == other.Z
            and self.id == other.id
        )


class Frozen:

    def __init__(self, X, id):
        self._hash = hash((X, id))
        self.X = X
        self.id = id

    def __repr__(self):
        if self.id == 0:
            return f'~{self.X}'
        else:
            return f'~{self.X}@{self.id}'

    def __hash__(self):
        return self._hash

    def __eq__(self, other):
        return (
            isinstance(other, Frozen)
            and self.X == other.X
            and self.id == other.id
        )


class Rule:

    def __init__(self, w, head, body):
        self.w = w
        self.head = head
        self.body = body
        self._hash = hash((head, body))

    def __iter__(self):
        return iter((self.head, self.body))

    def __eq__(self, other):
        return (isinstance(other, Rule)
                and self.w == other.w
                and self._hash == other._hash
                and other.head == self.head
                and other.body == self.body)

    def __hash__(self):
        return self._hash

    def __repr__(self):
        return f'{self.w}: {self.head} → {" ".join(map(str, self.body))}'


class Derivation:

    def __init__(self, r, x, *ys):
        assert isinstance(r, Rule) or r is None
        self.r = r
        self.x = x
        self.ys = ys

    # Warning: Currently, Derivations compare equal even if they have different rules.
    def __hash__(self):
#        return hash((self.r, self.x, self.ys))
        return hash((self.x, self.ys))

    def __eq__(self, other):
#        return (self.r, self.x, self.ys) == (other.r, other.x, other.ys)
        return isinstance(other, Derivation) and (self.x, self.ys) == (other.x, other.ys)

    def __repr__(self):
        open = colors.dark.white % '('
        close = colors.dark.white % ')'
        children = ' '.join(str(y) for y in self.ys)
        return f'{open}{self.x} {children}{close}'

    def weight(self):
        "Compute this weight this `Derivation`."
        W = self.r.w
        for y in self.ys:
            if isinstance(y, Derivation):
                W *= y.weight()
        return W

    def Yield(self):
        if isinstance(self, Derivation):
            return tuple(w for y in self.ys for w in Derivation.Yield(y))
        else:
            return (self,)

    def to_nltk(self):
        if not isinstance(self, Derivation): return self
        return nltk.Tree(str(self.x), [Derivation.to_nltk(y) for y in self.ys])

    def _repr_html_(self):
#        return f'<div style="text-align: center;"><span style="color: magenta;">{self.weight()}</span></br>{self.to_nltk()._repr_svg_()}</div>'
        return self.to_nltk()._repr_svg_()


class CFG:

    def __init__(self, R: 'semiring', S: 'start symbol', V: 'terminal vocabulary'):
        self.R = R      # semiring
        self.V = V      # alphabet
        self.N = {S}    # nonterminals
        self.S = S      # unique start symbol
        self.rules = [] # rules

    def __repr__(self):
        return "\n".join(f"{p}" for p in self)

    def _repr_html_(self):
        return f'<pre style="width: fit-content; text-align: left; border: thin solid black; padding: 0.5em;">{self}</pre>'

    @classmethod
    def from_string(cls, string, semiring, comment="#", start='S', is_terminal=lambda x: not x[0].isupper()):
        V = set()
        cfg = cls(R=semiring, S=start, V=V)
        string = string.replace('->', '→')   # synonym for the arrow
        for line in string.split('\n'):
            line = line.strip()
            if not line or line.startswith(comment): continue
            try:
                [(w, lhs, rhs)] = re.findall('(.*):\s*(\S+)\s*→\s*(.*)$', line)
                lhs = lhs.strip()
                rhs = rhs.strip().split()
                for x in rhs:
                    if is_terminal(x):
                        V.add(x)
                cfg.add(semiring.from_string(w), lhs, *rhs)
            except ValueError as e:
                raise ValueError(f'bad input line:\n{line}')
        return cfg

    def __call__(self, input):
        "Compute the total weight of the `input` sequence."
        return self._parse_chart(input)[0,self.S,len(input)]

    def _parse_chart(self, input):
        "Implements CKY algorithm for evaluating the total weight of the `input` sequence."
        if not self.in_cnf(): self = self.cnf
        (nullary, terminal, binary) = self._cnf()
        N = len(input)
        # nullary rule
        c = self.R.chart()
        for i in range(N+1):
            c[i,self.S,i] += nullary
        # preterminal rules
        for i in range(N):
            for r in terminal[input[i]]:
                c[i,r.head,i+1] += r.w
        # binary rules
        for span in range(1, N + 1):
            for i in range(N - span + 1):
                k = i + span
                for j in range(i + 1, k):
                    for r in binary:
                        X, [Y, Z] = r.head, r.body
                        c[i,X,k] +=  r.w * c[i,Y,j] * c[j,Z,k]
        return c

    def language(self, depth):
        "Enumerate strings generated by this cfg by derivations up to a the given `depth`."
        lang = self.R.chart()
        for d in self.derivations(self.S, depth):
            lang[d.Yield()] += d.weight()
        return lang

    @cached_property
    def rhs(self):
        rhs = defaultdict(list)
        for r in self:
            rhs[r.head].append(r)
        return rhs

    def is_terminal(self, x):
        return x in self.V

    def is_nonterminal(self, X):
        return not self.is_terminal(X)

    def __iter__(self):
        return iter(self.rules)

    @property
    def size(self):
        return sum(1 + len(r.body) for r in self)

    @property
    def num_rules(self):
        return len(self.rules)

    def spawn(self, *, R=None, S=None, V=None):
        return self.__class__(R=self.R if R is None else R,
                              S=self.S if S is None else S,
                              V=set(self.V) if V is None else V)

    def add(self, w, head, *body):
        if w == self.R.zero: return   # skip rules with weight zero
        self.N.add(head)
        r = Rule(w, head, body)
        self.rules.append(r)
        return r

    def rename(self, f):
        new = self.spawn(S = f(self.S))
        for r in self:
            new.add(r.w, f(r.head), *((y if self.is_terminal(y) else f(y)
                                       for y in r.body)))
        return new

    def assert_equal(self, other, verbose=False, throw=True):
        assert verbose or throw
        if isinstance(other, str): other = self.__class__.from_string(other, self.R)
        if verbose:
            # TODO: need to check the weights in the print out; we do it in the assertion
            S = set(self.rules)
            G = set(other.rules)
            for r in sorted(S | G, key=str):
                if r in S and r in G: continue
                #if r in S and r not in G: continue
                #if r not in S and r in G: continue
                print(
                    colors.mark(r in S),
                    #colors.mark(r in S and r in G),
                    colors.mark(r in G),
                    r,
                )
        assert not throw or Counter(self.rules) == Counter(other.rules), \
            f'\n\nhave=\n{str(self)}\nwant=\n{str(other)}'

    def treesum(self, **kwargs):
        return self.agenda()[self.S]

    def trim(self, bottomup_only=False):

        C = set(self.V)
        C.update(e.head for e in self.rules if len(e.body) == 0)

        incoming = defaultdict(list)
        outgoing = defaultdict(list)
        for e in self.rules:
            incoming[e.head].append(e)
            for b in e.body:
                outgoing[b].append(e)

        agenda = set(C)
        while agenda:
            x = agenda.pop()
            for e in outgoing[x]:
                if all((b in C) for b in e.body):
                    if e.head not in C:
                        C.add(e.head)
                        agenda.add(e.head)

        if bottomup_only: return self._trim(C)

        T = {self.S}
        agenda.update(T)
        while agenda:
            x = agenda.pop()
            for e in incoming[x]:
                #assert e.head in T
                for b in e.body:
                    if b not in T and b in C:
                        T.add(b)
                        agenda.add(b)

        return self._trim(T)

    def cotrim(self):
        return self.trim(bottomup_only=True)

    def _trim(self, symbols):
        new = self.spawn()
        for p in self:
            if p.head in symbols and p.w != self.R.zero and set(p.body) <= symbols:
                new.add(p.w, p.head, *p.body)
        return new

    def derivations(self, X, H):
        "Enumerate derivations of symbol X with height <= H"
        if X is None: X = self.S

        if self.is_terminal(X):
            yield X

        elif H <= 0:
            return

        else:
            for r in self.rhs[X]:
                for ys in self._derivations_list(r.body, H-1):
                    yield Derivation(r, X, *ys)

    def _derivations_list(self, X, H):
        if len(X) == 0:
            yield ()
        else:
            for x in self.derivations(X[0], H):
                for xs in self._derivations_list(X[1:], H):
                    yield (x, *xs)

    def derivations_of(self, s):
        "Enumeration of derivations with yield `s`"

        def p(X,I,K):
            if self.is_terminal(X):
                if K-I == 1 and s[I] == X:
                    yield X
                else:
                    return
            else:
                for r in self.rhs[X]:
                    for ys in ps(r.body, I, K):
                        yield Derivation(r, X, *ys)

        def ps(X,I,K):
            if len(X) == 0:
                if K-I == 0:
                    yield ()
            else:
                for J in range(I, K+1):
                    for x in p(X[0], I, J):
                        for xs in ps(X[1:], J, K):
                            yield (x, *xs)

        return p(self.S, 0, len(s))

    #___________________________________________________________________________
    # Transformations

    def _lehmann(self, N, W):
        "Lehmann's (1977) algorithm."

        V = W.copy()
        U = W.copy()

        for j in tqdm(N):
            V, U = U, V
            V = self.R.chart()
            s = U[j, j].star()
            for i in N:
                for k in N:
                    # i ➙ j ⇝ j ➙ k
                    V[i, k] = U[i, k] + U[i, j] * s * U[j, k]

        # add paths of length zero
        for i in N:
            V[i, i] += self.R.one

        return V

    def unaryremove(self):
        """
        Return an equivalent grammar with no unary rules.
        """

        # compute unary chain weights
        A = self.R.chart()
        for p in tqdm(self.rules):
            if len(p.body) == 1 and self.is_nonterminal(p.body[0]):
                A[p.body[0], p.head] += p.w

        W = self._lehmann(self.N, A)

        new = self.spawn()
        for p in tqdm(self.rules):
            X, body = p
            if len(body) == 1 and self.is_nonterminal(body[0]): continue
            for Y in self.N:
                new.add(W[X,Y]*p.w, Y, *body)

        return new

    def nullaryremove(self, binarize=True, **kwargs):
        """
        Return an equivalent grammar with no nullary rules except for one at the
        start symbol.
        """
        # A really wide rule can take a very long time because of the power set
        # in this rule so it is really important to binarize.
        if binarize: self = self.binarize()
        self = self.separate_start()
        return self._push_null_weights(self.null_weight(), **kwargs)

    def null_weight(self):
        """
        Compute the map from nonterminal to total weight of generating the
        empty string starting from that nonterminal.
        """
        ecfg = self.spawn(V=set())
        for p in self:
            if not any(self.is_terminal(y) for y in p.body):
                ecfg.add(p.w, p.head, *p.body)
        return ecfg.agenda()

    def null_weight_start(self):
        return self.null_weight()[self.S]

    def _push_null_weights(self, null_weight, recovery=False, rename=lambda x: f'${x}'):
        """Returns a grammar that generates the same weighted language but it is
        nullary-free at all nonterminals except its start symbol.  [Assumes that
        S does not appear on any RHS; call separate_start to ensure this.]

        The nonterminals with nonzero null_weight will be eliminated from the
        grammar.  They will be repaced with nullary-free variants that are
        marked according to `rename` (the default option is to mark them with a
        dollar sign prefix).

        Bonus (Hygiene property): Any nonterminal that survives the this
        transformation is guaranteed to generate the same weighted language.

        """

        # Warning: this method might have issues when `separate_start` hasn't
        # been run before.  So we run it rather than leaving it up to chance.
        assert self.S not in {y for r in self for y in r.body}

        def f(x):
            "Rename nonterminal if necessary"
            if null_weight[x] == self.R.zero or x == self.S:   # not necessary; keep old name
                return x
            else:
                return rename(x)

        rcfg = self.spawn()
        rcfg.add(null_weight[self.S], self.S)

        if recovery:
            for x in self.N:
                if f(x) == x: continue
                rcfg.add(null_weight[x], x)
                rcfg.add(self.R.one, x, f(x))

        for r in self:

            if len(r.body) == 0: continue  # drop nullary rule

            for B in product([0, 1], repeat=len(r.body)):
                v, new_body = r.w, []

                for i, b in enumerate(B):
                    if b:
                        v *= null_weight[r.body[i]]
                    else:
                        new_body.append(f(r.body[i]))

                # exclude the cases that would be new nullary rules!
                if len(new_body) > 0:
                    rcfg.add(v, f(r.head), *new_body)

        return rcfg

    def separate_start(self):
        "Ensure that the start symbol does not appear on the RHS of any rule."
        # create a new start symbol if the current one appears on the rhs of any existing rule
        if self.S in {y for r in self for y in r.body}:
            S = _gen_nt(self.S)
            new = self.spawn(S = S)
            # preterminal rules
            new.add(self.R.one, S, self.S)
            for r in self:
                new.add(r.w, r.head, *r.body)
            return new
        else:
            return self

    def separate_terminals(self):
        "Ensure that the each terminal is produced by a preterminal rule."
        one = self.R.one
        new = self.spawn()

        _preterminal = {}
        def preterminal(x):
            y = _preterminal.get(x)
            if y is None:
                y = new.add(one, _gen_nt(), x)
                _preterminal[x] = y
            return y

        for r in self:
            if len(r.body) == 1 and self.is_terminal(r.body[0]):
                new.add(r.w, r.head, *r.body)
            else:
                new.add(r.w, r.head, *((preterminal(y).head if y in self.V else y) for y in r.body))

        return new

    def binarize(self):
        new = self.spawn()

        stack = list(self.rules)
        while stack:
            p = stack.pop()
            if len(p.body) <= 2:
                new.add(p.w, p.head, *p.body)
            else:
                stack.extend(self._fold(p, [(0, 1)]))

        return new

    def _fold(self, p, I):

        # new productions
        P, heads = [], []
        for (i, j) in I:
            head = _gen_nt()
            heads.append(head)
            body = p.body[i:j+1]
            P.append(Rule(self.R.one, head, body))

        # new "head" production
        body = tuple()
        start = 0
        for (end, n), head in zip(I, heads):
            body += p.body[start:end] + (head,)
            start = n+1
        body += p.body[start:]
        P.append(Rule(p.w, p.head, body))

        return P

    @cached_property
    def cnf(self):
        new = self.separate_terminals().binarize().nullaryremove().unaryremove().trim()
        assert new.in_cnf()
        return new

    # TODO: make CNF grammars a speciazed subclass of CFG.
    def _cnf(self):
        nullary = self.R.zero
        terminal = defaultdict(list)
        binary = []
        for r in self:
            if len(r.body) == 0:
                nullary += r.w
                assert r.head == self.S
            elif len(r.body) == 1:
                terminal[r.body[0]].append(r)
                assert self.is_terminal(r.body[0])
            else:
                assert len(r.body) == 2
                binary.append(r)
                assert self.is_nonterminal(r.body[0])
                assert self.is_nonterminal(r.body[1])
        return (nullary, terminal, binary)

    def in_cnf(self):
        """check if grammar is in cnf"""
        for r in self:
            assert r.head in self.N
            if len(r.body) == 0 and r.head == self.S:
                continue
            elif len(r.body) == 1 and self.is_terminal(r.body[0]):
                continue
            elif len(r.body) == 2 and all(self.is_nonterminal(y) and y != self.S for y in r.body):
                continue
            else:
                return False
        return True

    def unfold(self, i, k):
        assert isinstance(i, int) and isinstance(k, int)
        s = self.rules[i]
        assert self.is_nonterminal(s.body[k])

        wp = self.R.zero
        new = self.spawn()
        for j, r in enumerate(self):
            if j != i:
                new.add(r.w, r.head, *r.body)

        for r in self.rhs[s.body[k]]:
            new.add(s.w*r.w, s.head, *s.body[:k], *r.body, *s.body[k+1:])

        return new

    def speculate(self, Xs, Ps=None, filter=True, id=0):
        """
        The speculation transformation as described in Opedal et al., (2023).
        """
        if Ps is None: Ps = self
        return Speculation(parent = self, Xs = Xs, Ps = Ps, filter = filter, id = id)

    def lc_generalized(self, Xs, Ps=None, filter=True, id=0):
        """
        The generalized left-corner transformation (Opedal et al., 2023)
        """
        if Ps is None: Ps = self
        return GLCT(parent = self, Xs = Xs, Ps = Ps, filter = filter, id = id)

    def lc_selective(self, Ps, filter=True):
        """
        The selective left-corner transformation (Johnson and Roark, 2000) with
        their top-down factoring optimization (see §2.5).
        """
        return self.lc_generalized(Ps=Ps, Xs=self.V | self.N, filter=filter)

    def agenda(self, max_iters=10000, tol=1e-12):
        "Agenda-based semi-naive evaluation"
        old = self.R.chart()

        # precompute the mapping from updates to where they need to go
        routing = defaultdict(list)
        for r in self.rules:
            for k in range(len(r.body)):
                routing[r.body[k]].append((r, k))

        change = self.R.chart()
        for a in self.V:
            change[a] += self.R.one

        for r in self.rules:
            if len(r.body) == 0:
                change[r.head] += r.w

        for _ in tqdm(range(max_iters)):
            if len(change) == 0: break
            u,v = change.popitem()

            new = old[u] + v

            if old[u].metric(new) <= tol: continue

            for r, k in routing[u]:

                W = r.w
                for j in range(len(r.body)):
                    if u == r.body[j]:
                        if j < k:    W *= new
                        elif j == k: W *= v
                        else:        W *= old[u]
                    else:
                        W *= old[r.body[j]]

                change[r.head] += W

            old[u] = new

        return old

    def naive_bottom_up(self, *, tol=1e-12, timeout=100_000):

        def _approx_equal(U, V):
            return all((self.R.metric(U[X], V[X]) <= tol) for X in self.N)

        R = self.R
        V = R.chart()
        counter = 0
        while counter < timeout:
            U = self._bottom_up_step(V)
            if _approx_equal(U, V): break
            V = U
            counter += 1
        return V

    def _bottom_up_step(self, V):
        R = self.R
        one = R.one
        U = R.chart()
        for a in self.V:
            U[a] = one
        for p in self.rules:
            update = p.w
            for X in p.body:
                if self.is_nonterminal(X):
                    update *= V[X]
            U[p.head] += update
        return U

    #___________________________________________________________________________
    # Left-recursion analysis and elimination methods

    def left_recursion_graph(self):
        "Left corner graph over symbols and all rules."
        return self._left_recursion_graph(self.rules)

    def _left_recursion_graph(self, Ps):
        """
        Return the left-corner graph over all symbols given rules `Ps` In this graph,
        the nodes are the symbols (N | V) and the edges are from `body[0] →
        head` for each rule in `Ps`.  For the head to body graph, simply call
        `graph.reverse()`.
        """
        G = nx.DiGraph()
        for x in self.N | self.V:
            G.add_node(x)
        for p in Ps:
            if len(p.body) == 0: continue
            G.add_edge(p.head, p.body[0], label=p)

        # TODO: use subclassing instead of this monkey-patch workaround
        def _repr_html_():
            # add a nicer visualization for notebooks
            GG = graphviz.Digraph(
                node_attr=dict(shape='record',fontname='Monospace', fontsize='10',
                               height='0', width='0', margin="0.055,0.042"),
                edge_attr=dict(arrowhead='vee', arrowsize='0.5',
                               fontname='Monospace', fontsize='9'),
            )
            for i,j in G.edges:
                GG.edge(str(i), str(j))
            for i in G.nodes:
                GG.node(str(i))
            return GG._repr_image_svg_xml()

        # monkeypatch a nicer visualization method for notebooks
        G._repr_html_ = _repr_html_

        return G

    def is_left_recursive(self):
        "Return true iff this grammar contains any cyclical left-recursion"
        return len(self.find_lr_rules()) != 0

    def find_lr_rules(self):
        """
        Return the set of left-recursive rules (i.e., those that appear in any
        cyclical left-recursive block)
        """
        # this utility flattens the list of sets returned by `find_lr_block`
        G = self.left_recursion_graph()
        H = nx.condensation(G)
        f = H.graph['mapping']
        return {r for r in self.rules if len(r.body) > 0 and f[r.head] == f[r.body[0]]}

    def sufficient_Xs(self, Ps):
        """
        Determine the set of left corner recognition symbols required for GLCT to
        eliminate left recursion according to Theorem 4.
        """
        return ((self.V | {p.head for p in set(self.rules) - set(Ps)})
                & {p.body[0] for p in Ps})

    def elim_left_recursion(self, **kwargs):
        "Eliminate left recursion from this grammar."
        Ps = self.find_lr_rules()
        return self.lc_generalized(Xs=self.sufficient_Xs(Ps), Ps=Ps, **kwargs)


class SlashNames:

    def _slash(self, X, Y):
        x = Slash(X, Y, id=0)
        if x not in self.parent.N: return x
        return Slash(X, Y, id=self.id)

    def _frozen(self, X):
        if self.is_terminal(X): return X
        x = Frozen(X, id=0)
        if x not in self.parent.N: return x
        return Frozen(X, id=self.id)

    def spawn(self, *, R=None, S=None, V=None):
        return CFG(R=self.R if R is None else R,
                   S=self.S if S is None else S,
                   V=set(self.V) if V is None else V)


class Speculation(SlashNames,CFG):

    def __init__(self, parent, Xs, Ps, filter, id):
        assert set(Ps) <= set(parent.rules)
        assert all(len(r.body) > 0 for r in Ps)

        super().__init__(R=parent.R, S=parent.S, V=set(parent.V))

        self.Xs = Xs
        self.Ps = Ps
        self.filter = filter
        self.id = id
        self.parent = parent

        slash = self._slash; frozen = self._frozen; one = self.R.one
        add = self.add

        # slash base case
        for X in (Xs if filter else (parent.V | parent.N)):
            add(one, slash(X, X))

        # make slashed and frozen rules
        for p in parent:
            (head, body) = p

            if p not in Ps:
                # frozen base case
                add(p.w, frozen(head), *body)
            else:

                # slash recursive case
                for X in (Xs if filter else (parent.N | parent.V)):
                    add(p.w, slash(head, X), slash(body[0], X), *body[1:])

                # frozen recursive case
                if body[0] not in Xs:
                    add(p.w, frozen(head), frozen(body[0]), *body[1:])

        # recovery rules
        for Y in parent.N - Xs:
            add(one, Y, frozen(Y))

        for Y in parent.N:
            for X in Xs:
                add(one, Y, frozen(X), slash(Y, X))

    def mapping(self, d):

        f = self.mapping
        frozen = self._frozen
        slash = self._slash

        if not isinstance(d, Derivation):
            assert self.is_terminal(d)
            return d

        elif d.r not in self.Ps:
            # frozen base case
            rest = map(f, d.ys)
            if d.x in self.Xs:
                return tree(d.x, tree(frozen(d.x), *rest), tree(slash(d.x, d.x)))
            else:
                return tree(d.x, tree(frozen(d.x), *rest))

        else:
            dd = f(d.ys[0])
            rest = map(f, d.ys[1:])

            # special handling for the case of a terminal
            if not isinstance(dd, Derivation):
                o = dd
                if o in self.Xs:
                    dd = tree(o, o, tree(slash(o, o)))
                else:
                    dd = tree(o, o)

            if len(dd.ys) == 1:   # frozen
                [o] = dd.ys
                # slash base case; this is the bottommost element of Xs along the spine.
                if d.x in self.Xs:
                    return tree(d.x, tree(frozen(d.x), o, *rest), tree(slash(d.x, d.x)))
                else:
                    return tree(d.x, tree(frozen(d.x), o, *rest))

            else:
                [o, s] = dd.ys
                name = (o.x.X if isinstance(o.x, Frozen) else o.x) if isinstance(o, Derivation) else o
                return tree(d.x, o, tree(slash(d.x, name), s, *rest))


class GLCT(SlashNames, CFG):

    def __init__(self, parent, Xs, Ps, filter, id):
        assert set(Ps) <= set(parent.rules)
        assert all(len(r.body) > 0 for r in Ps)

        super().__init__(R=parent.R, S=parent.S, V=set(parent.V))

        self.Xs = Xs
        self.Ps = Ps
        self.filter = filter
        self.id = id
        self.parent = parent

        # TODO: to ensure fresh symbols, use the following.
        #slash = lambda X,Y: Slash(X,Y,id)
        #frozen = lambda X: X if self.is_terminal(X) else Frozen(X,id)

        slash = self._slash; frozen = self._frozen; one = self.R.one
        add = self.add

        Xs = set(Xs)

        if filter:

            # `retained` is the set of symbols that appear outside the
            # left-corner paths. These items may need recovery rules.
            retained = {parent.S}
            for p in parent:
                for X in p.body[int(p in Ps):]:
                    if parent.is_nonterminal(X):
                        retained.add(X)

            # Left corner graph over symbols, but only the rules in Ps.
            G = parent._left_recursion_graph(Ps).reverse()
            T = nx.transitive_closure(G, reflexive=True)

            # `den2num` represents {Y: (den ⇝ num) the left edge}
            den2num = {den: {num for _, num in T.edges(den)} for den in parent.N | parent.V}

            # below is the set of (retained) numerators that are reachable from the denominators
            useful_num = {num for den in Xs for num in den2num[den] if num in retained}

            # In GLCT, we create a rule for each possible consumer of the left corner
            num_given_den = lambda den: (den2num.get(den, set()) & retained)

            # den in Xs ~~> mid ~~~> num in retained
            useful_mid = {mid
                          for den in Xs
                          for mid in den2num[den]
                          for num in den2num[mid]
                          if num in retained}

        else:
            retained = parent.N
            num_given_den = lambda _: parent.N
            useful_num = parent.N | parent.V
            useful_mid = parent.N | parent.V

        # base case
        for X in useful_num:
            add(one, slash(X, X))

        # make slashed and frozen rules
        for p in parent:
            (head, body) = p
            if p not in Ps:
                add(p.w, frozen(head), *body)
            else:
                for Y in num_given_den(body[0]):
                    if body[0] not in useful_mid: continue
                    add(p.w, slash(Y, body[0]), *body[1:], slash(Y, head))
                if body[0] not in Xs:
                    add(p.w, frozen(head), frozen(body[0]), *body[1:])

        # recovery rules
        for Y in retained - Xs:
            add(one, Y, frozen(Y))
        for X in Xs:
            for Y in num_given_den(X):
                add(one, Y, frozen(X), slash(Y, X))

    @cached_property
    def _speculation(self):
        return self.parent.speculate(Xs=self.Xs, Ps=self.Ps, filter=self.filter, id=self.id)

    def mapping(self, d):
        # Our implementation uses the speculation mapping followed by a
        # transpose mapping on the slashed items.
        return self._mapping(self._speculation.mapping(d))

    def _mapping(self, d):
        "Helper method; transposes the slash items."
        if not isinstance(d, Derivation):
            return d
        elif isinstance(d.x, Slash):
            spine = []
            rests = []
            curr = d
            while len(curr.ys) != 0:
                assert isinstance(curr.x, Slash)
                spine.append(curr.ys[0].x)
                rests.append(tuple(map(self._mapping, curr.ys[1:])))
                curr = curr.ys[0]
            num = d.x.Y
            new = tree(self._slash(num, num))
            for rest, s in zip(rests, spine):
                new = tree(self._slash(num, s.Y), *rest, new)
            return new
        else:
            return tree(d.x, *map(self._mapping, d.ys))

    def elim_nullary_slash(self, binarize=True):
        """
        Optimized method for eliminating nullary rules created by the
        left-corner and speculation transformations; should match `nullaryremove`.
        """
        if binarize: self = self.binarize()

        W = self.R.chart()
        v = self.R.chart()

        for p in self:
            head, body = p

            # unary slash
            if len(body) == 1 and isinstance(head, Slash):
                assert isinstance(body[0], Slash)
                W[head, body[0]] += p.w

            # nullary slash
            if len(body) == 0 and isinstance(head, Slash):
                v[head] += p.w

            # This optimized method assumes that the grammar prior to
            # transformation is nullary free.  If the assertion below fails,
            # then so does that assumption.
            assert not len(body) == 0 or isinstance(head, Slash), p

        K = self._lehmann(self.N, W)

        null_weight = self.R.chart()
        for X in self.N:
            for Y in self.N:
                null_weight[X] += K[X,Y] * v[Y]

        return self._push_null_weights(null_weight)


def tree(x, *ys):
    r = Rule(None, x, tuple(label(y) for y in ys))
    return Derivation(r, x, *ys)


def label(d):
    return d.x if isinstance(d, Derivation) else d
