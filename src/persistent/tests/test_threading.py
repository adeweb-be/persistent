"""Tests for concurrent access to persistent objects and the pickle cache.

These tests exercise the C extensions under multi-threaded conditions,
particularly on the free-threaded build of Python (3.14t and later):

- Per_dealloc removes the cache entry of an object while other threads
  use the same cache.
- A lookup must not return an object that another thread is freeing.
- Sweeps, deactivation and attribute access change the LRU ring of one
  cache from several threads.

The tests are also valuable on standard (GIL) Python to catch
concurrency issues with greenlets or signal handlers.
"""

import gc
import struct
import threading
from unittest import TestCase

from persistent.tests.utils import skipIfNoCExtension


def _make_oid(n):
    return struct.pack(">Q", n)


@skipIfNoCExtension
class ConcurrentCacheTests(TestCase):
    """Test concurrent access to the C PickleCache."""

    def _getTargetClass(self):
        from persistent.cPickleCache import PickleCache
        return PickleCache

    def _makePersistentClass(self):
        from persistent import Persistent

        class P(Persistent):
            pass

        return P

    def _makeJar(self):
        jar = _DummyJar()
        jar._cache = self._getTargetClass()(jar, 100)
        jar.cache = jar._cache
        return jar

    def _addObjects(self, jar, start, count):
        """Add `count` persistent objects to jar, starting at oid `start`."""
        P = self._makePersistentClass()
        objects = []
        for i in range(start, start + count):
            obj = P()
            obj._p_oid = _make_oid(i)
            obj._p_jar = jar
            jar._cache[obj._p_oid] = obj
            objects.append(obj)
        return objects

    def test_concurrent_object_creation_and_deletion(self):
        """Multiple threads creating and deleting objects in the cache."""
        jar = self._makeJar()
        errors = []
        n_threads = 4
        n_objects = 200

        def worker(thread_id):
            try:
                start = thread_id * n_objects
                objs = self._addObjects(jar, start, n_objects)
                # Drop references so GC can collect them
                del objs
                gc.collect()
            except Exception as e:
                errors.append((thread_id, e))

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive(), "Thread hung")
        self.assertEqual(errors, [], f"Errors in threads: {errors}")

    def test_concurrent_ghost_creation_and_gc(self):
        """Threads create ghost objects while GC runs concurrently.

        This is the scenario that triggered the infinite recursion in
        Per_dealloc on free-threaded Python: ghost objects have stolen
        (uncounted) references in the cache dict, and GC triggers
        deallocation that calls cc_oid_unreferenced -> PyDict_GetItem,
        which INCREFs/DECREFs the value internally.
        """
        jar = self._makeJar()
        errors = []
        barrier = threading.Barrier(3)

        def creator(thread_id):
            try:
                barrier.wait(timeout=5)
                for cycle in range(50):
                    start = thread_id * 10000 + cycle * 100
                    objs = self._addObjects(jar, start, 100)
                    # Make them all ghosts by deactivating
                    for obj in objs:
                        obj._p_deactivate()
                    del objs
            except Exception as e:
                errors.append((thread_id, e))

        def collector():
            try:
                barrier.wait(timeout=5)
                for _ in range(100):
                    gc.collect()
            except Exception as e:
                errors.append(("gc", e))

        threads = [
            threading.Thread(target=creator, args=(0,)),
            threading.Thread(target=creator, args=(1,)),
            threading.Thread(target=collector),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive(), "Thread hung")
        self.assertEqual(errors, [], f"Errors in threads: {errors}")

    def test_concurrent_incrgc(self):
        """Cache.incrgc() running while objects are added/removed."""
        jar = self._makeJar()
        errors = []
        stop = threading.Event()

        def adder():
            try:
                i = 0
                while not stop.is_set():
                    objs = self._addObjects(jar, i, 50)
                    for obj in objs:
                        obj._p_deactivate()
                    del objs
                    i += 50
            except Exception as e:
                errors.append(("adder", e))

        def gc_runner():
            try:
                for _ in range(200):
                    jar._cache.incrgc()
            except Exception as e:
                errors.append(("gc_runner", e))
            finally:
                stop.set()

        threads = [
            threading.Thread(target=adder),
            threading.Thread(target=gc_runner),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive(), "Thread hung")
        self.assertEqual(errors, [], f"Errors in threads: {errors}")

    def test_concurrent_minimize(self):
        """Cache.minimize() running concurrently with object access."""
        jar = self._makeJar()
        errors = []
        # Pre-populate
        objs = self._addObjects(jar, 0, 500)
        for obj in objs:
            obj._p_changed = True  # activate
            obj._p_changed = False  # mark as clean/uptodate
        del objs

        stop = threading.Event()

        def accessor():
            try:
                while not stop.is_set():
                    for oid_num in range(500):
                        oid = _make_oid(oid_num)
                        obj = jar._cache.get(oid)
                        if obj is not None:
                            # Touch to simulate access
                            try:
                                obj._p_activate()
                            except Exception:
                                pass  # object may have been evicted
            except Exception as e:
                errors.append(("accessor", e))

        def minimizer():
            try:
                for _ in range(50):
                    jar._cache.minimize()
            except Exception as e:
                errors.append(("minimizer", e))
            finally:
                stop.set()

        threads = [
            threading.Thread(target=accessor),
            threading.Thread(target=minimizer),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive(), "Thread hung")
        self.assertEqual(errors, [], f"Errors in threads: {errors}")

    def test_concurrent_cache_clear_during_access(self):
        """Clearing the entire cache while threads access objects."""
        jar = self._makeJar()
        errors = []

        def populate_and_clear(cycle):
            try:
                start = cycle * 200
                objs = self._addObjects(jar, start, 200)
                for obj in objs:
                    obj._p_changed = True
                    obj._p_changed = False
                del objs
                jar._cache.full_sweep()
            except Exception as e:
                errors.append((cycle, e))

        threads = [
            threading.Thread(target=populate_and_clear, args=(i,))
            for i in range(8)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive(), "Thread hung")
        self.assertEqual(errors, [], f"Errors in threads: {errors}")

    def test_lookup_during_final_decref(self):
        """Look up oids while another thread frees the objects.

        This is the race of a connection pool: one thread releases the
        last references to the objects of a cache while the next thread
        loads the same oids. A lookup must not return an object that is
        being freed. A new ghost can then take the oid.
        """
        self._check_lookup_during_final_decref(self._makePersistentClass())

    def test_lookup_during_final_decref_with_finalizer(self):
        """The same race, with a class that defines __del__."""

        class P(self._makePersistentClass()):
            def __del__(self):
                """Does nothing."""

        self._check_lookup_during_final_decref(P)

    def _check_lookup_during_final_decref(self, P):
        jar = self._makeJar()
        cache = jar._cache
        n_oids = 100
        errors = []
        stop = threading.Event()
        oids = [_make_oid(i) for i in range(n_oids)]

        def load():
            for oid in oids:
                obj = cache.get(oid)
                if obj is None:
                    obj = P()
                    try:
                        cache.new_ghost(oid, obj)
                    except ValueError:
                        # Another thread loaded this oid first.
                        continue
                if obj._p_oid != oid:
                    errors.append(("wrong object", oid, obj._p_oid))
                yield obj

        def holder():
            # Hold the ghosts, then free them all at once.
            try:
                for _ in range(100):
                    objs = list(load())
                    del objs
            except Exception as e:
                errors.append(("holder", e))
            finally:
                stop.set()

        def loader():
            # Load each oid and drop it at once.
            try:
                while not stop.is_set():
                    for obj in load():
                        del obj
            except Exception as e:
                errors.append(("loader", e))

        threads = [threading.Thread(target=holder)]
        threads += [threading.Thread(target=loader) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive(), "Thread hung")
        self.assertEqual(errors, [], f"Errors in threads: {errors}")
        gc.collect()
        # Each freed object removed its entry.
        self.assertEqual(len(cache), 0)

    def test_attribute_access_during_sweep(self):
        """Read attributes while another thread sweeps the cache.

        An attribute access moves the object in the ring of its cache.
        DB.cacheMinimize() sweeps the caches of connections that other
        threads use. The two threads use different objects of one cache.
        """
        jar = self._makeJar()
        cache = jar._cache

        class P(self._makePersistentClass()):
            attr = 42

        def add(start):
            objs = []
            for i in range(start, start + 200):
                obj = P()
                obj._p_oid = _make_oid(i)
                obj._p_jar = jar
                cache[obj._p_oid] = obj
                objs.append(obj)
            return objs

        # The sweeps skip changed objects.
        read_objs = add(0)
        for obj in read_objs:
            obj._p_changed = True
        swept_objs = add(1000)
        errors = []
        stop = threading.Event()

        def reader():
            try:
                while not stop.is_set():
                    for obj in read_objs:
                        if obj.attr != 42:
                            errors.append(("reader", obj.attr))
            except Exception as e:
                errors.append(("reader", e))

        def sweeper():
            try:
                for _ in range(100):
                    for obj in swept_objs:
                        obj._p_activate()
                    cache.minimize()
                    cache.incrgc()
            except Exception as e:
                errors.append(("sweeper", e))
            finally:
                stop.set()

        threads = [
            threading.Thread(target=reader),
            threading.Thread(target=sweeper),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive(), "Thread hung")
        self.assertEqual(errors, [], f"Errors in threads: {errors}")
        self.assertEqual(cache.ringlen(), len(read_objs))
        self.assertEqual(cache.cache_non_ghost_count, len(read_objs))

    def test_rapid_create_destroy_cycle(self):
        """Rapidly create and destroy objects to stress refcount handling.

        On free-threaded Python, this exercises the removal of cache
        entries in Per_dealloc while other threads add entries.
        """
        jar = self._makeJar()
        errors = []
        n_threads = 4
        n_cycles = 100

        def worker(thread_id):
            try:
                P = self._makePersistentClass()
                for cycle in range(n_cycles):
                    oid = _make_oid(thread_id * n_cycles + cycle)
                    obj = P()
                    obj._p_oid = oid
                    obj._p_jar = jar
                    jar._cache[oid] = obj
                    # Immediately drop and collect
                    del obj
                    gc.collect()
            except Exception as e:
                errors.append((thread_id, e))

        threads = [
            threading.Thread(target=worker, args=(i,))
            for i in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
            self.assertFalse(t.is_alive(), "Thread hung")
        self.assertEqual(errors, [], f"Errors in threads: {errors}")


class _DummyJar:
    """Minimal jar for threading tests.

    Implements just enough for PickleCache and Persistent to work.
    """

    def __init__(self):
        self._registered = []

    def register(self, obj):
        self._registered.append(obj)

    def setstate(self, obj):
        # Trivial: just mark as up-to-date
        pass
