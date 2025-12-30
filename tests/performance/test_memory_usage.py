"""
Performance tests for memory usage, leaks, and optimization.
Tests memory management, garbage collection, and resource cleanup.
"""

import asyncio
import ctypes
import gc
import logging
import multiprocessing
import os
import random
import sys
import time
import threading
import tracemalloc
import uuid
from collections import defaultdict, deque
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import psutil
import pytest
from pympler import asizeof, muppy, summary

from microagents.agents.base import BaseAgent
from microagents.core.cache import LRUCache, MemoryCache
from microagents.core.pool import ObjectPool, ResourcePool
from microagents.core.memory import MemoryManager, MemoryPressureDetector
from microagents.dsl.compiler import DSLCompiler
from microagents.dsl.parser import DSLASTParser

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class TestMemoryUsage:
    """Performance tests for memory usage and management."""
    
    # Constants
    LARGE_DATASET_SIZE = 10_000  # Number of records for large dataset tests
    CONCURRENT_THREADS = 50      # Number of concurrent threads
    MEMORY_LIMIT_MB = 1024       # Memory limit for tests (1GB)
    TEST_ITERATIONS = 100        # Iterations for leak detection
    
    @dataclass
    class MemoryStats:
        """Container for memory statistics."""
        timestamp: datetime
        rss_mb: float            # Resident Set Size
        vms_mb: float            # Virtual Memory Size
        shared_mb: float         # Shared memory
        uss_mb: float            # Unique Set Size
        pss_mb: float            # Proportional Set Size
        swap_mb: float           # Swap usage
        percent: float           # Memory usage percentage
        available_mb: float      # Available memory
        free_mb: float           # Free memory
        used_mb: float           # Used memory
        
        @classmethod
        def current(cls):
            """Get current memory statistics."""
            process = psutil.Process()
            memory_info = process.memory_info()
            memory_percent = process.memory_percent()
            
            # Get system memory
            system_memory = psutil.virtual_memory()
            
            return cls(
                timestamp=datetime.now(),
                rss_mb=memory_info.rss / 1024 / 1024,
                vms_mb=memory_info.vms / 1024 / 1024,
                shared_mb=memory_info.shared / 1024 / 1024,
                uss_mb=getattr(memory_info, 'uss', 0) / 1024 / 1024 if hasattr(memory_info, 'uss') else 0,
                pss_mb=getattr(memory_info, 'pss', 0) / 1024 / 1024 if hasattr(memory_info, 'pss') else 0,
                swap_mb=memory_info.swap / 1024 / 1024 if hasattr(memory_info, 'swap') else 0,
                percent=memory_percent,
                available_mb=system_memory.available / 1024 / 1024,
                free_mb=system_memory.free / 1024 / 1024,
                used_mb=system_memory.used / 1024 / 1024
            )
    
    @dataclass
    class ObjectTracker:
        """Track object creation and destruction."""
        objects_created: int = 0
        objects_destroyed: int = 0
        peak_object_count: int = 0
        current_object_count: int = 0
        
        def object_created(self):
            self.objects_created += 1
            self.current_object_count += 1
            self.peak_object_count = max(self.peak_object_count, self.current_object_count)
        
        def object_destroyed(self):
            self.objects_destroyed += 1
            self.current_object_count -= 1
    
    class LeakyAgent(BaseAgent):
        """Agent that intentionally leaks memory for testing."""
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.leaky_data = []  # Intentionally hold references
            self.circular_refs = {}  # Circular references
            
        async def execute(self, inputs, context):
            # Create some data that won't be cleaned up
            data = {
                'id': str(uuid.uuid4()),
                'timestamp': datetime.now(),
                'payload': 'x' * 1024,  # 1KB payload
                'nested': {
                    'level1': {'level2': {'level3': 'deep' * 256}}
                }
            }
            
            # Add to leaky storage
            self.leaky_data.append(data)
            
            # Create circular reference
            circular = {'self_ref': None}
            circular['self_ref'] = circular
            self.circular_refs[data['id']] = circular
            
            return {'processed': True, 'data_id': data['id']}
        
        def clear_leaks(self):
            """Manually clear leaked references."""
            self.leaky_data.clear()
            self.circular_refs.clear()
            gc.collect()
    
    class MemoryIntensiveAgent(BaseAgent):
        """Agent that uses significant memory."""
        
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.data_cache = {}
            self.numpy_arrays = []
            
        async def execute(self, inputs, context):
            # Create large data structures
            array_size = inputs.get('array_size', 1000)
            
            # Large NumPy array
            np_array = np.random.rand(array_size, array_size)
            self.numpy_arrays.append(np_array)
            
            # Large dictionary
            large_dict = {f'key_{i}': 'value' * 100 for i in range(1000)}
            self.data_cache[str(uuid.uuid4())] = large_dict
            
            # Pandas DataFrame
            df = pd.DataFrame({
                'col1': np.random.rand(10000),
                'col2': np.random.rand(10000),
                'col3': ['data' * 10 for _ in range(10000)]
            })
            
            return {
                'array_shape': np_array.shape,
                'dict_size': len(large_dict),
                'df_shape': df.shape,
                'memory_used_mb': self._get_memory_usage()
            }
        
        def _get_memory_usage(self):
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024
        
        def cleanup(self):
            """Cleanup memory-intensive resources."""
            self.data_cache.clear()
            self.numpy_arrays.clear()
            gc.collect()
    
    @pytest.fixture
    def memory_manager(self):
        """Fixture providing memory manager."""
        return MemoryManager(
            memory_limit_mb=self.MEMORY_LIMIT_MB,
            gc_threshold_percent=80.0,
            emergency_cleanup_percent=90.0
        )
    
    @pytest.fixture
    def memory_pressure_detector(self):
        """Fixture providing memory pressure detector."""
        return MemoryPressureDetector(
            pressure_threshold=70.0,
            high_pressure_threshold=85.0,
            critical_threshold=95.0
        )
    
    @pytest.fixture
    def leaky_agent(self):
        """Fixture providing leaky agent."""
        return self.LeakyAgent(
            agent_id="leaky_agent_1",
            name="Leaky Agent",
            version="1.0.0"
        )
    
    @pytest.fixture
    def memory_intensive_agent(self):
        """Fixture providing memory-intensive agent."""
        return self.MemoryIntensiveAgent(
            agent_id="memory_intensive_agent_1",
            name="Memory Intensive Agent",
            version="1.0.0"
        )
    
    @pytest.fixture
    def lru_cache(self):
        """Fixture providing LRU cache."""
        return LRUCache(
            max_size=1000,
            max_memory_mb=100
        )
    
    @pytest.fixture
    def object_pool(self):
        """Fixture providing object pool."""
        return ObjectPool(
            factory=lambda: {'id': str(uuid.uuid4()), 'data': None},
            max_size=100,
            cleanup_timeout=60
        )
    
    @pytest.fixture
    def object_tracker(self):
        """Fixture providing object tracker."""
        return self.ObjectTracker()
    
    @contextmanager
    def track_memory(self, description: str = "operation"):
        """
        Context manager to track memory usage.
        
        Args:
            description: Description of the operation being tracked
        """
        start_stats = self.MemoryStats.current()
        start_time = time.time()
        
        logger.info(f"Memory tracking started: {description}")
        
        try:
            yield
        finally:
            end_time = time.time()
            end_stats = self.MemoryStats.current()
            
            duration = end_time - start_time
            memory_increase = end_stats.rss_mb - start_stats.rss_mb
            
            logger.info(f"Memory tracking completed: {description}")
            logger.info(f"  Duration: {duration:.2f}s")
            logger.info(f"  Memory increase: {memory_increase:+.2f} MB")
            logger.info(f"  Peak RSS: {end_stats.rss_mb:.2f} MB")
            
            if memory_increase > 10:  # More than 10MB increase
                logger.warning(f"  Significant memory increase detected: {memory_increase:.2f} MB")
    
    @contextmanager
    def limit_memory(self, max_memory_mb: int):
        """
        Context manager to limit memory usage.
        
        Args:
            max_memory_mb: Maximum memory in MB
        """
        import resource
        
        # Convert MB to bytes
        max_memory_bytes = max_memory_mb * 1024 * 1024
        
        # Get current limits
        old_soft, old_hard = resource.getrlimit(resource.RLIMIT_AS)
        
        try:
            # Set new limit
            resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, old_hard))
            logger.info(f"Memory limit set: {max_memory_mb} MB")
            yield
        finally:
            # Restore old limit
            resource.setrlimit(resource.RLIMIT_AS, (old_soft, old_hard))
            logger.info(f"Memory limit restored")
    
    @contextmanager
    def tracemalloc_context(self):
        """Context manager for tracemalloc memory tracking."""
        tracemalloc.start()
        
        try:
            yield
        finally:
            snapshot = tracemalloc.take_snapshot()
            tracemalloc.stop()
            
            # Display top memory consumers
            top_stats = snapshot.statistics('lineno')
            
            logger.info("Top memory consumers:")
            for stat in top_stats[:10]:  # Top 10
                logger.info(f"  {stat.traceback.format()[-1]}: {stat.size / 1024:.1f} KB")
    
    # Test Group 1: Memory Leak Detection
    class TestMemoryLeakDetection:
        """Tests for memory leak detection."""
        
        def test_simple_memory_leak_detection(self, leaky_agent):
            """Test detection of simple memory leaks."""
            with TestMemoryUsage().track_memory("Simple memory leak detection"):
                initial_stats = TestMemoryUsage.MemoryStats.current()
                
                # Execute agent multiple times (should leak memory)
                for i in range(100):
                    asyncio.run(leaky_agent.execute({}, {}))
                
                # Force garbage collection
                gc.collect()
                
                final_stats = TestMemoryUsage.MemoryStats.current()
                memory_increase = final_stats.rss_mb - initial_stats.rss_mb
                
                # Should show memory increase (leak)
                assert memory_increase > 1.0  # At least 1MB increase
                
                logger.info(f"Memory leak test:")
                logger.info(f"  Initial RSS: {initial_stats.rss_mb:.2f} MB")
                logger.info(f"  Final RSS: {final_stats.rss_mb:.2f} MB")
                logger.info(f"  Memory increase: {memory_increase:.2f} MB")
                
                # Cleanup
                leaky_agent.clear_leaks()
                gc.collect()
                
                cleanup_stats = TestMemoryUsage.MemoryStats.current()
                logger.info(f"  After cleanup: {cleanup_stats.rss_mb:.2f} MB")
        
        def test_circular_reference_leak_detection(self):
            """Test detection of memory leaks from circular references."""
            with TestMemoryUsage().track_memory("Circular reference leak detection"):
                # Create circular references
                circular_objects = []
                
                for i in range(1000):
                    obj1 = {}
                    obj2 = {'ref': obj1}
                    obj1['ref'] = obj2  # Circular reference
                    circular_objects.append((obj1, obj2))
                
                # Track memory before and after cleanup
                before_cleanup = TestMemoryUsage.MemoryStats.current()
                
                # Break circular references
                for obj1, obj2 in circular_objects:
                    obj1['ref'] = None
                    obj2['ref'] = None
                
                # Clear list
                circular_objects.clear()
                
                # Force GC
                gc.collect()
                
                after_cleanup = TestMemoryUsage.MemoryStats.current()
                memory_reduction = before_cleanup.rss_mb - after_cleanup.rss_mb
                
                logger.info(f"Circular reference test:")
                logger.info(f"  Before cleanup: {before_cleanup.rss_mb:.2f} MB")
                logger.info(f"  After cleanup: {after_cleanup.rss_mb:.2f} MB")
                logger.info(f"  Memory reduction: {memory_reduction:.2f} MB")
        
        def test_gc_collect_effectiveness(self):
            """Test effectiveness of garbage collection."""
            import objgraph
            
            with TestMemoryUsage().track_memory("GC effectiveness test"):
                # Create many objects
                objects = []
                for i in range(10000):
                    obj = {
                        'id': i,
                        'data': 'x' * 1000,
                        'nested': {'key': 'value' * 100}
                    }
                    objects.append(obj)
                
                # Count objects before cleanup
                before_count = len(gc.get_objects())
                
                # Clear references and collect
                objects.clear()
                gc.collect()
                
                # Count objects after cleanup
                after_count = len(gc.get_objects())
                reduction = before_count - after_count
                
                logger.info(f"GC effectiveness test:")
                logger.info(f"  Objects before GC: {before_count:,}")
                logger.info(f"  Objects after GC: {after_count:,}")
                logger.info(f"  Objects collected: {reduction:,}")
                logger.info(f"  Reduction: {(reduction / before_count * 100):.1f}%")
                
                # Should collect significant number of objects
                assert reduction > 5000  # Should collect at least half
        
        def test_weak_reference_memory_management(self):
            """Test memory management with weak references."""
            import weakref
            
            with TestMemoryUsage().track_memory("Weak reference memory management"):
                # Create objects with strong references
                strong_refs = [{'id': i, 'data': 'x' * 100} for i in range(1000)]
                
                # Create weak references to same objects
                weak_refs = [weakref.ref(obj) for obj in strong_refs]
                
                # Memory usage with strong references
                with_strong = TestMemoryUsage.MemoryStats.current()
                
                # Clear strong references
                strong_refs.clear()
                gc.collect()
                
                # Check weak references
                alive_count = sum(1 for ref in weak_refs if ref() is not None)
                
                # Memory usage after clearing strong references
                after_clear = TestMemoryUsage.MemoryStats.current()
                memory_reduction = with_strong.rss_mb - after_clear.rss_mb
                
                logger.info(f"Weak reference test:")
                logger.info(f"  Objects with strong refs: 1000")
                logger.info(f"  Objects still alive via weak refs: {alive_count}")
                logger.info(f"  Memory reduction: {memory_reduction:.2f} MB")
                
                # Weak references should not keep objects alive
                assert alive_count == 0
        
        def test_memory_leak_in_async_tasks(self):
            """Test memory leaks in async tasks."""
            async def create_leaky_task(task_id):
                # Simulate task that leaks memory
                data = {'task_id': task_id, 'payload': 'x' * 1024}
                # Not properly cleaned up
                return data
            
            async def run_leaky_tasks():
                tasks = []
                for i in range(100):
                    task = asyncio.create_task(create_leaky_task(i))
                    tasks.append(task)
                
                # Wait for completion but don't clean up tasks
                await asyncio.gather(*tasks)
                
                # Tasks remain in event loop
                return len(asyncio.all_tasks())
            
            with TestMemoryUsage().track_memory("Async task memory leak"):
                # Run async tasks
                task_count = asyncio.run(run_leaky_tasks())
                
                # Check for hanging tasks
                logger.info(f"Async task leak test:")
                logger.info(f"  Tasks in event loop: {task_count}")
                
                # Clean up event loop
                loop = asyncio.get_event_loop()
                for task in asyncio.all_tasks(loop):
                    task.cancel()
                
                # Should have many tasks (potential leak)
                assert task_count > 10
    
    # Test Group 2: Garbage Collection Analysis
    class TestGarbageCollectionAnalysis:
        """Tests for garbage collection analysis."""
        
        def test_gc_generation_analysis(self):
            """Test analysis of garbage collection generations."""
            with TestMemoryUsage().track_memory("GC generation analysis"):
                # Get GC stats before creating objects
                gc.collect()
                before_stats = {
                    'gen0': gc.get_count()[0],
                    'gen1': gc.get_count()[1],
                    'gen2': gc.get_count()[2]
                }
                
                # Create objects that will go through generations
                objects = []
                for generation in [0, 1, 2]:
                    for i in range(1000):
                        obj = {'gen': generation, 'data': 'x' * 100}
                        objects.append(obj)
                    
                    # Force collection of specific generation
                    if generation < 2:
                        gc.collect(generation)
                
                # Get GC stats after
                after_stats = {
                    'gen0': gc.get_count()[0],
                    'gen1': gc.get_count()[1],
                    'gen2': gc.get_count()[2]
                }
                
                logger.info(f"GC generation analysis:")
                logger.info(f"  Before - Gen0: {before_stats['gen0']}, Gen1: {before_stats['gen1']}, Gen2: {before_stats['gen2']}")
                logger.info(f"  After  - Gen0: {after_stats['gen0']}, Gen1: {after_stats['gen1']}, Gen2: {after_stats['gen2']}")
                
                # Objects should promote through generations
                assert after_stats['gen2'] > before_stats['gen2']
        
        def test_gc_threshold_adjustment(self):
            """Test adjustment of GC thresholds."""
            with TestMemoryUsage().track_memory("GC threshold adjustment"):
                # Get current thresholds
                old_thresholds = gc.get_threshold()
                
                # Set aggressive thresholds for testing
                gc.set_threshold(50, 100, 200)
                new_thresholds = gc.get_threshold()
                
                # Create many objects to trigger GC
                objects = []
                for i in range(10000):
                    obj = {'id': i, 'data': 'x' * 100}
                    objects.append(obj)
                    
                    # Delete every other object to create garbage
                    if i % 2 == 0:
                        objects[-1] = None
                
                # Force collection
                gc.collect()
                
                # Restore thresholds
                gc.set_threshold(*old_thresholds)
                
                logger.info(f"GC threshold adjustment:")
                logger.info(f"  Old thresholds: {old_thresholds}")
                logger.info(f"  New thresholds: {new_thresholds}")
                logger.info(f"  Objects created: {10000}")
                logger.info(f"  GC counts: {gc.get_count()}")
        
        def test_gc_debug_leak_detection(self):
            """Test GC debug features for leak detection."""
            with TestMemoryUsage().track_memory("GC debug leak detection"):
                # Enable debug flags
                old_flags = gc.get_debug()
                gc.set_debug(gc.DEBUG_SAVEALL)  # Save all garbage
                
                try:
                    # Create garbage
                    garbage = []
                    for i in range(100):
                        obj = {'id': i, 'data': 'x' * 1024}
                        garbage.append(obj)
                    
                    # Delete references
                    garbage.clear()
                    
                    # Collect garbage
                    collected = gc.collect()
                    
                    # Check garbage list
                    garbage_objects = gc.garbage
                    
                    logger.info(f"GC debug leak detection:")
                    logger.info(f"  Objects collected: {collected}")
                    logger.info(f"  Garbage list size: {len(garbage_objects)}")
                    
                    # Clean up garbage list
                    del garbage_objects[:]
                    
                finally:
                    # Restore debug flags
                    gc.set_debug(old_flags)
        
        def test_gc_finalizer_behavior(self):
            """Test behavior of object finalizers."""
            class ObjectWithFinalizer:
                def __init__(self, obj_id):
                    self.obj_id = obj_id
                    self.data = 'x' * 1024
                    self.finalizer_called = False
                
                def __del__(self):
                    # Finalizer
                    self.finalizer_called = True
                    # Note: Don't do heavy work in finalizers!
            
            with TestMemoryUsage().track_memory("GC finalizer behavior"):
                objects = []
                finalizer_calls = []
                
                for i in range(100):
                    obj = ObjectWithFinalizer(i)
                    objects.append(obj)
                    finalizer_calls.append(obj.finalizer_called)
                
                # Delete references
                objects.clear()
                
                # Force collection (may not run finalizers immediately)
                gc.collect()
                
                # Check finalizer status
                # Note: Finalizers may not have run yet
                logger.info(f"GC finalizer behavior:")
                logger.info(f"  Objects created: 100")
                logger.info(f"  Finalizers defined: 100")
                
                # Run finalizers explicitly
                gc.collect()
                for obj in gc.garbage:
                    if hasattr(obj, '__del__'):
                        try:
                            obj.__del__()
                        except:
                            pass
        
        def test_gc_memory_pressure_response(self):
            """Test GC response to memory pressure."""
            with TestMemoryUsage().track_memory("GC memory pressure response"):
                # Simulate memory pressure by allocating lots of memory
                memory_blocks = []
                
                try:
                    # Allocate memory in chunks
                    chunk_size = 10 * 1024 * 1024  # 10MB
                    for i in range(20):  # Try to allocate 200MB
                        try:
                            block = bytearray(chunk_size)
                            memory_blocks.append(block)
                            logger.info(f"  Allocated chunk {i + 1}: {len(memory_blocks) * 10} MB")
                        except MemoryError:
                            logger.warning(f"  MemoryError at chunk {i + 1}")
                            break
                    
                    # Check GC activity
                    gc_counts_before = gc.get_count()
                    
                    # Create more pressure
                    more_objects = []
                    for i in range(10000):
                        obj = {'id': i, 'data': 'x' * 1000}
                        more_objects.append(obj)
                    
                    gc_counts_after = gc.get_count()
                    
                    logger.info(f"GC memory pressure response:")
                    logger.info(f"  Memory allocated: {len(memory_blocks) * 10} MB")
                    logger.info(f"  GC before: {gc_counts_before}")
                    logger.info(f"  GC after: {gc_counts_after}")
                    logger.info(f"  Collections triggered: {sum(a - b for a, b in zip(gc_counts_after, gc_counts_before))}")
                    
                finally:
                    # Clean up
                    memory_blocks.clear()
                    gc.collect()
    
    # Test Group 3: Cache Memory Usage
    class TestCacheMemoryUsage:
        """Tests for cache memory usage."""
        
        def test_lru_cache_memory_usage(self, lru_cache):
            """Test memory usage of LRU cache."""
            with TestMemoryUsage().track_memory("LRU cache memory usage"):
                # Fill cache with large objects
                cache_size = 100
                for i in range(cache_size):
                    key = f"key_{i}"
                    value = {
                        'id': i,
                        'data': 'x' * 1024,  # 1KB per value
                        'timestamp': datetime.now()
                    }
                    lru_cache.set(key, value)
                
                # Measure cache memory
                cache_stats = lru_cache.get_stats()
                
                logger.info(f"LRU cache memory usage:")
                logger.info(f"  Cache size: {cache_stats['size']}")
                logger.info(f"  Memory limit: {cache_stats['memory_limit_mb']} MB")
                logger.info(f"  Memory usage: {cache_stats['memory_usage_mb']:.2f} MB")
                logger.info(f"  Hit rate: {cache_stats['hit_rate']:.2%}")
                
                # Verify cache respects memory limit
                assert cache_stats['memory_usage_mb'] <= cache_stats['memory_limit_mb']
        
        def test_cache_eviction_memory_release(self, lru_cache):
            """Test memory release during cache eviction."""
            with TestMemoryUsage().track_memory("Cache eviction memory release"):
                initial_stats = TestMemoryUsage.MemoryStats.current()
                
                # Fill cache beyond limit
                cache_size = 200  # More than cache capacity (100)
                for i in range(cache_size):
                    key = f"key_{i}"
                    value = {'data': 'x' * 1024 * 10}  # 10KB per value
                    lru_cache.set(key, value)
                
                after_fill_stats = TestMemoryUsage.MemoryStats.current()
                
                # Access some keys to cause eviction
                for i in range(50):
                    lru_cache.get(f"key_{i}")
                
                after_access_stats = TestMemoryUsage.MemoryStats.current()
                
                memory_increase = after_fill_stats.rss_mb - initial_stats.rss_mb
                memory_reduction = after_fill_stats.rss_mb - after_access_stats.rss_mb
                
                logger.info(f"Cache eviction test:")
                logger.info(f"  Initial memory: {initial_stats.rss_mb:.2f} MB")
                logger.info(f"  After fill: {after_fill_stats.rss_mb:.2f} MB")
                logger.info(f"  After access/eviction: {after_access_stats.rss_mb:.2f} MB")
                logger.info(f"  Memory increase on fill: {memory_increase:.2f} MB")
                logger.info(f"  Memory reduction after eviction: {memory_reduction:.2f} MB")
                
                # Should release some memory after eviction
                assert memory_reduction > 0
        
        def test_cache_timeout_memory_cleanup(self):
            """Test memory cleanup of expired cache entries."""
            from microagents.core.cache import TTLCache
            
            with TestMemoryUsage().track_memory("Cache timeout memory cleanup"):
                cache = TTLCache(max_size=100, ttl_seconds=1)  # 1 second TTL
                
                # Fill cache
                for i in range(50):
                    cache.set(f"key_{i}", {'data': 'x' * 1024})
                
                before_expiry_stats = TestMemoryUsage.MemoryStats.current()
                
                # Wait for expiration
                time.sleep(2)
                
                # Access should trigger cleanup
                for i in range(50):
                    cache.get(f"key_{i}")
                
                after_cleanup_stats = TestMemoryUsage.MemoryStats.current()
                
                # Force cleanup
                cache.cleanup()
                
                after_force_cleanup_stats = TestMemoryUsage.MemoryStats.current()
                
                logger.info(f"Cache timeout cleanup:")
                logger.info(f"  Before expiry: {before_expiry_stats.rss_mb:.2f} MB")
                logger.info(f"  After access cleanup: {after_cleanup_stats.rss_mb:.2f} MB")
                logger.info(f"  After force cleanup: {after_force_cleanup_stats.rss_mb:.2f} MB")
                
                # Cache should have cleaned up expired entries
                cache_stats = cache.get_stats()
                logger.info(f"  Cache size after cleanup: {cache_stats['size']}")
        
        def test_cache_memory_fragmentation(self):
            """Test memory fragmentation in cache."""
            with TestMemoryUsage().track_memory("Cache memory fragmentation"):
                cache = LRUCache(max_size=1000)
                
                # Add varying sized objects
                sizes = [10, 100, 1000, 10000]  # bytes
                
                for i in range(100):
                    size = random.choice(sizes)
                    key = f"key_{i}"
                    value = 'x' * size
                    cache.set(key, value)
                
                # Remove random objects
                for i in random.sample(range(100), 30):
                    key = f"key_{i}"
                    cache.delete(key)
                
                # Add more objects of different sizes
                for i in range(100, 150):
                    size = random.choice(sizes)
                    key = f"key_{i}"
                    value = 'x' * size
                    cache.set(key, value)
                
                cache_stats = cache.get_stats()
                
                logger.info(f"Cache fragmentation test:")
                logger.info(f"  Cache size: {cache_stats['size']}")
                logger.info(f"  Memory usage: {cache_stats['memory_usage_mb']:.2f} MB")
                logger.info(f"  Operations: {cache_stats['operations']}")
                
                # Fragmentation is hard to measure directly, but we can
                # check if memory usage is reasonable
                assert cache_stats['memory_usage_mb'] < 10.0  # Should be less than 10MB
        
        def test_cache_concurrent_memory_usage(self, lru_cache):
            """Test memory usage under concurrent cache access."""
            import concurrent.futures
            
            with TestMemoryUsage().track_memory("Concurrent cache memory usage"):
                def cache_worker(worker_id):
                    """Worker function for concurrent cache access."""
                    operations = 100
                    for i in range(operations):
                        key = f"worker_{worker_id}_key_{i}"
                        value = {'worker': worker_id, 'data': 'x' * 100}
                        
                        # Set value
                        lru_cache.set(key, value)
                        
                        # Get value (50% chance)
                        if random.random() > 0.5:
                            lru_cache.get(key)
                    
                    return operations
                
                # Run concurrent workers
                with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
                    futures = [executor.submit(cache_worker, i) for i in range(10)]
                    total_operations = sum(f.result() for f in concurrent.futures.as_completed(futures))
                
                cache_stats = lru_cache.get_stats()
                
                logger.info(f"Concurrent cache test:")
                logger.info(f"  Workers: 10")
                logger.info(f"  Total operations: {total_operations}")
                logger.info(f"  Cache hits: {cache_stats['hits']}")
                logger.info(f"  Cache misses: {cache_stats['misses']}")
                logger.info(f"  Hit rate: {cache_stats['hit_rate']:.2%}")
                logger.info(f"  Memory usage: {cache_stats['memory_usage_mb']:.2f} MB")
                
                # Should handle concurrent access without excessive memory growth
                assert cache_stats['memory_usage_mb'] < lru_cache.max_memory_mb
    
    # Test Group 4: Peak Memory Measurement
    class TestPeakMemoryMeasurement:
        """Tests for peak memory measurement."""
        
        def test_peak_memory_usage_tracking(self):
            """Test tracking of peak memory usage."""
            with TestMemoryUsage().track_memory("Peak memory usage tracking"):
                process = psutil.Process()
                
                # Get initial peak
                initial_peak = process.memory_info().rss
                
                # Allocate memory in phases
                memory_blocks = []
                peaks = []
                
                for phase in range(5):
                    # Allocate 50MB
                    block = bytearray(50 * 1024 * 1024)  # 50MB
                    memory_blocks.append(block)
                    
                    # Get current memory
                    current = process.memory_info().rss
                    peaks.append(current)
                    
                    logger.info(f"  Phase {phase + 1}: {current / 1024 / 1024:.1f} MB")
                
                # Find actual peak
                actual_peak = max(peaks)
                peak_mb = actual_peak / 1024 / 1024
                
                # Clean up
                memory_blocks.clear()
                gc.collect()
                
                logger.info(f"Peak memory tracking:")
                logger.info(f"  Initial: {initial_peak / 1024 / 1024:.1f} MB")
                logger.info(f"  Peak: {peak_mb:.1f} MB")
                logger.info(f"  Increase: {(actual_peak - initial_peak) / 1024 / 1024:.1f} MB")
                
                # Should show significant peak
                assert peak_mb > initial_peak / 1024 / 1024 + 100  # At least 100MB increase
        
        def test_memory_usage_over_time(self):
            """Test memory usage patterns over time."""
            with TestMemoryUsage().track_memory("Memory usage over time"):
                import matplotlib
                matplotlib.use('Agg')  # Non-interactive backend
                import matplotlib.pyplot as plt
                
                # Track memory over time
                time_points = []
                memory_points = []
                
                process = psutil.Process()
                
                # Pattern: allocate, hold, release
                for cycle in range(3):
                    # Allocate
                    logger.info(f"Cycle {cycle + 1}: Allocating...")
                    blocks = [bytearray(20 * 1024 * 1024) for _ in range(5)]  # 100MB total
                    
                    time_points.append(time.time())
                    memory_points.append(process.memory_info().rss / 1024 / 1024)
                    
                    # Hold for a bit
                    time.sleep(0.5)
                    time_points.append(time.time())
                    memory_points.append(process.memory_info().rss / 1024 / 1024)
                    
                    # Release
                    logger.info(f"Cycle {cycle + 1}: Releasing...")
                    blocks.clear()
                    gc.collect()
                    
                    time_points.append(time.time())
                    memory_points.append(process.memory_info().rss / 1024 / 1024)
                    
                    # Pause between cycles
                    time.sleep(0.5)
                
                # Analyze pattern
                memory_changes = []
                for i in range(1, len(memory_points)):
                    change = memory_points[i] - memory_points[i-1]
                    memory_changes.append(change)
                
                logger.info(f"Memory usage over time:")
                logger.info(f"  Time points: {len(time_points)}")
                logger.info(f"  Max memory: {max(memory_points):.1f} MB")
                logger.info(f"  Min memory: {min(memory_points):.1f} MB")
                logger.info(f"  Avg memory: {sum(memory_points)/len(memory_points):.1f} MB")
                logger.info(f"  Largest increase: {max(memory_changes):.1f} MB")
                logger.info(f"  Largest decrease: {min(memory_changes):.1f} MB")
                
                # Should show clear allocation/release pattern
                assert max(memory_changes) > 50  # Significant increases
                assert min(memory_changes) < -50  # Significant decreases
        
        def test_memory_spike_detection(self):
            """Test detection of memory spikes."""
            with TestMemoryUsage().track_memory("Memory spike detection"):
                process = psutil.Process()
                
                # Monitor memory with high frequency
                samples = 100
                interval = 0.01  # 10ms
                
                memory_readings = []
                
                for i in range(samples):
                    # Create spike at sample 50
                    if i == 50:
                        spike_data = bytearray(100 * 1024 * 1024)  # 100MB spike
                    
                    memory = process.memory_info().rss / 1024 / 1024
                    memory_readings.append(memory)
                    
                    time.sleep(interval)
                    
                    # Clean spike
                    if i == 50:
                        del spike_data
                        gc.collect()
                
                # Detect spikes
                spikes = []
                for i in range(1, len(memory_readings) - 1):
                    prev = memory_readings[i-1]
                    curr = memory_readings[i]
                    next_val = memory_readings[i+1]
                    
                    # Spike if current is significantly higher than neighbors
                    if curr > prev * 1.5 and curr > next_val * 1.5:
                        spikes.append((i, curr))
                
                logger.info(f"Memory spike detection:")
                logger.info(f"  Samples: {samples}")
                logger.info(f"  Sampling interval: {interval*1000:.0f} ms")
                logger.info(f"  Max reading: {max(memory_readings):.1f} MB")
                logger.info(f"  Min reading: {min(memory_readings):.1f} MB")
                logger.info(f"  Spikes detected: {len(spikes)}")
                
                for spike_idx, spike_value in spikes:
                    logger.info(f"    Spike at sample {spike_idx}: {spike_value:.1f} MB")
                
                # Should detect the spike we created
                assert len(spikes) >= 1
        
        def test_memory_baseline_comparison(self):
            """Test comparison against memory baseline."""
            with TestMemoryUsage().track_memory("Memory baseline comparison"):
                # Establish baseline
                baseline_samples = []
                for _ in range(10):
                    gc.collect()
                    stats = TestMemoryUsage.MemoryStats.current()
                    baseline_samples.append(stats.rss_mb)
                    time.sleep(0.1)
                
                baseline_avg = sum(baseline_samples) / len(baseline_samples)
                baseline_std = np.std(baseline_samples) if len(baseline_samples) > 1 else 0
                
                logger.info(f"Baseline established:")
                logger.info(f"  Average: {baseline_avg:.1f} MB")
                logger.info(f"  Std Dev: {baseline_std:.1f} MB")
                logger.info(f"  Range: {min(baseline_samples):.1f} - {max(baseline_samples):.1f} MB")
                
                # Test operation
                test_data = []
                for i in range(5):
                    data = bytearray(10 * 1024 * 1024)  # 10MB
                    test_data.append(data)
                    
                    current = TestMemoryUsage.MemoryStats.current().rss_mb
                    deviation = abs(current - baseline_avg)
                    
                    logger.info(f"  Test point {i + 1}: {current:.1f} MB (deviation: {deviation:.1f} MB)")
                    
                    # Check if significantly above baseline
                    if deviation > baseline_std * 3:  # 3 sigma
                        logger.warning(f"    Significant deviation detected!")
                
                # Cleanup
                test_data.clear()
                gc.collect()
                
                # Verify return to baseline
                final = TestMemoryUsage.MemoryStats.current().rss_mb
                final_deviation = abs(final - baseline_avg)
                
                logger.info(f"Final check: {final:.1f} MB (deviation: {final_deviation:.1f} MB)")
                
                # Should return close to baseline
                assert final_deviation < baseline_std * 2
        
        def test_memory_usage_per_operation(self, memory_intensive_agent):
            """Test memory usage per operation."""
            with TestMemoryUsage().track_memory("Memory per operation"):
                # Run agent multiple times
                runs = 10
                memory_per_run = []
                
                for run in range(runs):
                    start_stats = TestMemoryUsage.MemoryStats.current()
                    
                    # Execute agent
                    asyncio.run(memory_intensive_agent.execute({'array_size': 500}, {}))
                    
                    end_stats = TestMemoryUsage.MemoryStats.current()
                    memory_used = end_stats.rss_mb - start_stats.rss_mb
                    memory_per_run.append(memory_used)
                    
                    # Cleanup
                    memory_intensive_agent.cleanup()
                    gc.collect()
                    
                    logger.info(f"  Run {run + 1}: {memory_used:.2f} MB")
                
                # Analyze
                avg_memory = sum(memory_per_run) / len(memory_per_run)
                std_memory = np.std(memory_per_run) if len(memory_per_run) > 1 else 0
                
                logger.info(f"Memory per operation analysis:")
                logger.info(f"  Runs: {runs}")
                logger.info(f"  Average memory per run: {avg_memory:.2f} MB")
                logger.info(f"  Std Dev: {std_memory:.2f} MB")
                logger.info(f"  Min: {min(memory_per_run):.2f} MB")
                logger.info(f"  Max: {max(memory_per_run):.2f} MB")
                
                # Memory usage should be consistent
                assert std_memory < avg_memory * 0.5  # Less than 50% variation
    
    # Test Group 5: Memory Fragmentation Testing
    class TestMemoryFragmentation:
        """Tests for memory fragmentation."""
        
        def test_memory_fragmentation_simulation(self):
            """Test simulation of memory fragmentation."""
            with TestMemoryUsage().track_memory("Memory fragmentation simulation"):
                # Simulate fragmentation by allocating and freeing blocks of varying sizes
                blocks = []
                allocations = []
                
                # Allocation pattern that causes fragmentation
                for i in range(100):
                    # Vary block sizes
                    if i % 3 == 0:
                        size = 1024  # 1KB
                    elif i % 3 == 1:
                        size = 10 * 1024  # 10KB
                    else:
                        size = 100 * 1024  # 100KB
                    
                    # Allocate
                    block = bytearray(size)
                    blocks.append(block)
                    allocations.append((i, size, 'allocated'))
                
                # Free every other block (creates fragmentation)
                for i in range(0, len(blocks), 2):
                    blocks[i] = None
                    allocations.append((i, 0, 'freed'))
                
                # Force GC
                gc.collect()
                
                # Try to allocate large contiguous block (might fail due to fragmentation)
                try:
                    large_block = bytearray(500 * 1024)  # 500KB
                    allocations.append(('large', 500 * 1024, 'allocated'))
                    large_success = True
                except MemoryError:
                    large_success = False
                    allocations.append(('large', 500 * 1024, 'failed'))
                
                # Clean up
                blocks.clear()
                gc.collect()
                
                logger.info(f"Memory fragmentation simulation:")
                logger.info(f"  Total allocations: {len([a for a in allocations if a[2] == 'allocated'])}")
                logger.info(f"  Freed blocks: {len([a for a in allocations if a[2] == 'freed'])}")
                logger.info(f"  Large allocation successful: {large_success}")
                
                # Try defragmentation by allocating all memory and freeing
                if not large_success:
                    logger.info("  Fragmentation likely occurred")
        
        def test_object_size_fragmentation(self):
            """Test fragmentation caused by varying object sizes."""
            with TestMemoryUsage().track_memory("Object size fragmentation"):
                # Create objects of varying sizes
                objects = []
                size_categories = {'small': 0, 'medium': 0, 'large': 0}
                
                for i in range(1000):
                    # Random size
                    size = random.randint(10, 10000)
                    
                    if size < 100:
                        category = 'small'
                    elif size < 1000:
                        category = 'medium'
                    else:
                        category = 'large'
                    
                    size_categories[category] += 1
                    
                    # Create object
                    obj = {
                        'id': i,
                        'data': 'x' * size,
                        'category': category
                    }
                    objects.append(obj)
                
                # Delete random objects (creates holes)
                for _ in range(300):
                    idx = random.randint(0, len(objects) - 1)
                    objects[idx] = None
                
                # Force GC
                gc.collect()
                
                # Count remaining objects by category
                remaining = {'small': 0, 'medium': 0, 'large': 0}
                for obj in objects:
                    if obj is not None:
                        remaining[obj['category']] += 1
                
                logger.info(f"Object size fragmentation:")
                logger.info(f"  Created - Small: {size_categories['small']}, Medium: {size_categories['medium']}, Large: {size_categories['large']}")
                logger.info(f"  Remaining - Small: {remaining['small']}, Medium: {remaining['medium']}, Large: {remaining['large']}")
                logger.info(f"  Fragmentation ratio: {(300 / 1000) * 100:.1f}%")
        
        def test_memory_pool_fragmentation_prevention(self, object_pool):
            """Test memory pool for fragmentation prevention."""
            with TestMemoryUsage().track_memory("Memory pool fragmentation prevention"):
                # Use object pool to reduce fragmentation
                pool_objects = []
                
                # Acquire and release objects from pool
                for i in range(100):
                    obj = object_pool.acquire()
                    obj['data'] = 'x' * 1024  # 1KB data
                    pool_objects.append(obj)
                
                # Release half of them
                for i in range(0, len(pool_objects), 2):
                    object_pool.release(pool_objects[i])
                    pool_objects[i] = None
                
                # Acquire more (should reuse released objects)
                for i in range(50):
                    obj = object_pool.acquire()
                    obj['data'] = 'y' * 1024
                    pool_objects.append(obj)
                
                pool_stats = object_pool.get_stats()
                
                logger.info(f"Memory pool test:")
                logger.info(f"  Pool size: {pool_stats['pool_size']}")
                logger.info(f"  Objects created: {pool_stats['objects_created']}")
                logger.info(f"  Objects reused: {pool_stats['objects_reused']}")
                logger.info(f"  Reuse rate: {pool_stats['reuse_rate']:.1%}")
                
                # Pool should enable reuse
                assert pool_stats['reuse_rate'] > 0
        
        def test_arena_allocation_fragmentation(self):
            """Test arena allocation to reduce fragmentation."""
            # Note: Python doesn't have built-in arena allocation,
            # but we can simulate with pre-allocated blocks
            
            class MemoryArena:
                def __init__(self, block_size=65536):  # 64KB blocks
                    self.block_size = block_size
                    self.blocks = []
                    self.current_block = None
                    self.current_offset = 0
                
                def allocate(self, size):
                    if self.current_block is None or (self.current_offset + size > self.block_size):
                        # Allocate new block
                        self.current_block = bytearray(self.block_size)
                        self.blocks.append(self.current_block)
                        self.current_offset = 0
                    
                    # Allocate from current block
                    offset = self.current_offset
                    self.current_offset += size
                    return memoryview(self.current_block)[offset:offset+size]
            
            with TestMemoryUsage().track_memory("Arena allocation"):
                arena = MemoryArena(block_size=65536)  # 64KB blocks
                
                # Allocate many small objects
                allocations = []
                for i in range(1000):
                    size = random.randint(10, 1000)
                    allocation = arena.allocate(size)
                    allocations.append(allocation)
                
                logger.info(f"Arena allocation test:")
                logger.info(f"  Blocks allocated: {len(arena.blocks)}")
                logger.info(f"  Total allocations: {len(allocations)}")
                logger.info(f"  Block size: {arena.block_size / 1024:.0f} KB")
                
                # Arena should consolidate allocations
                assert len(arena.blocks) < 100  # Much fewer than 1000 individual allocations
        
        def test_fragmentation_impact_on_performance(self):
            """Test performance impact of memory fragmentation."""
            with TestMemoryUsage().track_memory("Fragmentation performance impact"):
                import timeit
                
                # Setup for unfragmented memory
                def unfragmented_operation():
                    # Allocate and immediately free
                    data = [bytearray(1000) for _ in range(100)]
                    return len(data)
                
                # Setup for fragmented memory
                fragmented_data = []
                def fragmented_operation():
                    # Allocate with existing fragmentation
                    data = [bytearray(random.randint(10, 10000)) for _ in range(100)]
                    fragmented_data.extend(data)
                    
                    # Free some randomly
                    for i in random.sample(range(len(fragmented_data)), min(50, len(fragmented_data))):
                        fragmented_data[i] = None
                    
                    return len(data)
                
                # Time both operations
                unfragmented_time = timeit.timeit(unfragmented_operation, number=100)
                fragmented_time = timeit.timeit(fragmented_operation, number=100)
                
                # Cleanup
                fragmented_data.clear()
                gc.collect()
                
                logger.info(f"Fragmentation performance impact:")
                logger.info(f"  Unfragmented time: {unfragmented_time:.3f}s")
                logger.info(f"  Fragmented time: {fragmented_time:.3f}s")
                logger.info(f"  Performance difference: {(fragmented_time - unfragmented_time) / unfragmented_time * 100:.1f}%")
                
                # Fragmented operations might be slower
                # Note: This can vary based on Python implementation and OS
    
    # Test Group 6: Large Dataset Handling
    class TestLargeDatasetHandling:
        """Tests for handling large datasets."""
        
        def test_large_dataframe_memory_usage(self):
            """Test memory usage with large pandas DataFrames."""
            with TestMemoryUsage().track_memory("Large DataFrame memory usage"):
                # Create large DataFrame
                n_rows = 1000000  # 1 million rows
                n_cols = 10
                
                logger.info(f"Creating DataFrame with {n_rows:,} rows x {n_cols} columns...")
                
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                # Create DataFrame
                df = pd.DataFrame(
                    np.random.randn(n_rows, n_cols),
                    columns=[f'col_{i}' for i in range(n_cols)]
                )
                
                after_creation_stats = TestMemoryUsage.MemoryStats.current()
                
                # Add string column (memory intensive)
                df['text_col'] = ['text' * 10] * n_rows
                
                after_text_stats = TestMemoryUsage.MemoryStats.current()
                
                # Perform operations
                df['sum_col'] = df.sum(axis=1)
                df['mean_col'] = df.mean(axis=1)
                
                after_operations_stats = TestMemoryUsage.MemoryStats.current()
                
                # Memory usage breakdown
                creation_memory = after_creation_stats.rss_mb - start_stats.rss_mb
                text_memory = after_text_stats.rss_mb - after_creation_stats.rss_mb
                operations_memory = after_operations_stats.rss_mb - after_text_stats.rss_mb
                total_memory = after_operations_stats.rss_mb - start_stats.rss_mb
                
                logger.info(f"Large DataFrame memory usage:")
                logger.info(f"  DataFrame shape: {df.shape}")
                logger.info(f"  Creation memory: {creation_memory:.1f} MB")
                logger.info(f"  Text column memory: {text_memory:.1f} MB")
                logger.info(f"  Operations memory: {operations_memory:.1f} MB")
                logger.info(f"  Total memory increase: {total_memory:.1f} MB")
                
                # Check memory info from pandas
                df_memory = df.memory_usage(deep=True).sum() / 1024 / 1024
                logger.info(f"  Pandas reported memory: {df_memory:.1f} MB")
                
                # Cleanup
                del df
                gc.collect()
        
        def test_large_numpy_array_memory(self):
            """Test memory usage with large numpy arrays."""
            with TestMemoryUsage().track_memory("Large NumPy array memory"):
                # Test different array sizes and dtypes
                test_cases = [
                    ('float64_1M', np.float64, 1000000),
                    ('float32_1M', np.float32, 1000000),
                    ('int32_1M', np.int32, 1000000),
                    ('int8_1M', np.int8, 1000000),
                    ('float64_10M', np.float64, 10000000),
                ]
                
                for name, dtype, size in test_cases:
                    start_stats = TestMemoryUsage.MemoryStats.current()
                    
                    # Create array
                    arr = np.random.randn(size).astype(dtype)
                    
                    end_stats = TestMemoryUsage.MemoryStats.current()
                    memory_used = end_stats.rss_mb - start_stats.rss_mb
                    
                    # Theoretical memory
                    theoretical_mb = arr.nbytes / 1024 / 1024
                    
                    logger.info(f"  {name}:")
                    logger.info(f"    Size: {size:,}")
                    logger.info(f"    Dtype: {dtype}")
                    logger.info(f"    Theoretical memory: {theoretical_mb:.1f} MB")
                    logger.info(f"    Actual memory increase: {memory_used:.1f} MB")
                    logger.info(f"    Efficiency: {(theoretical_mb / memory_used * 100 if memory_used > 0 else 0):.1f}%")
                    
                    # Cleanup
                    del arr
                    gc.collect()
        
        def test_memory_mapped_file_handling(self):
            """Test memory-mapped file handling for large datasets."""
            import tempfile
            
            with TestMemoryUsage().track_memory("Memory-mapped file handling"):
                # Create temporary file
                with tempfile.NamedTemporaryFile(mode='w+b', suffix='.dat', delete=False) as tmp:
                    tmp_path = tmp.name
                
                try:
                    # Create memory-mapped array
                    shape = (10000, 10000)  # 100M elements
                    dtype = np.float32
                    
                    # Memory usage before
                    before_stats = TestMemoryUsage.MemoryStats.current()
                    
                    # Create memory-mapped array
                    mmap_arr = np.memmap(
                        tmp_path,
                        dtype=dtype,
                        mode='w+',
                        shape=shape
                    )
                    
                    # Write data
                    mmap_arr[:] = np.random.randn(*shape).astype(dtype)
                    mmap_arr.flush()
                    
                    # Memory usage after writing
                    after_write_stats = TestMemoryUsage.MemoryStats.current()
                    
                    # Read back
                    mmap_read = np.memmap(
                        tmp_path,
                        dtype=dtype,
                        mode='r',
                        shape=shape
                    )
                    
                    # Access slice (should not load entire file)
                    slice_data = mmap_read[1000:2000, 1000:2000]
                    
                    after_read_stats = TestMemoryUsage.MemoryStats.current()
                    
                    write_memory = after_write_stats.rss_mb - before_stats.rss_mb
                    read_memory = after_read_stats.rss_mb - after_write_stats.rss_mb
                    
                    logger.info(f"Memory-mapped file test:")
                    logger.info(f"  Array shape: {shape}")
                    logger.info(f"  Dtype: {dtype}")
                    logger.info(f"  File size: {os.path.getsize(tmp_path) / 1024 / 1024:.1f} MB")
                    logger.info(f"  Memory for writing: {write_memory:.1f} MB")
                    logger.info(f"  Memory for reading slice: {read_memory:.1f} MB")
                    logger.info(f"  Slice accessed: {slice_data.shape}")
                    
                    # Memory-mapped should use less memory than full array
                    full_array_memory = np.prod(shape) * np.dtype(dtype).itemsize / 1024 / 1024
                    logger.info(f"  Full array memory would be: {full_array_memory:.1f} MB")
                    
                finally:
                    # Cleanup
                    if 'mmap_arr' in locals():
                        del mmap_arr
                    if 'mmap_read' in locals():
                        del mmap_read
                    
                    gc.collect()
                    
                    # Delete temp file
                    try:
                        os.unlink(tmp_path)
                    except:
                        pass
        
        def test_chunked_data_processing(self):
            """Test memory-efficient chunked data processing."""
            with TestMemoryUsage().track_memory("Chunked data processing"):
                total_rows = 1000000
                chunk_size = 10000
                
                results = []
                memory_samples = []
                
                for chunk_start in range(0, total_rows, chunk_size):
                    chunk_end = min(chunk_start + chunk_size, total_rows)
                    
                    # Memory before chunk
                    before_stats = TestMemoryUsage.MemoryStats.current()
                    
                    # Process chunk
                    chunk = np.random.randn(chunk_end - chunk_start, 10)
                    chunk_result = np.mean(chunk, axis=0)
                    results.append(chunk_result)
                    
                    # Memory after chunk
                    after_stats = TestMemoryUsage.MemoryStats.current()
                    memory_samples.append(after_stats.rss_mb - before_stats.rss_mb)
                    
                    # Clear chunk
                    del chunk
                
                # Aggregate results
                final_result = np.mean(results, axis=0)
                
                logger.info(f"Chunked processing test:")
                logger.info(f"  Total rows: {total_rows:,}")
                logger.info(f"  Chunk size: {chunk_size:,}")
                logger.info(f"  Chunks processed: {len(results)}")
                logger.info(f"  Average memory per chunk: {np.mean(memory_samples):.2f} MB")
                logger.info(f"  Max memory per chunk: {np.max(memory_samples):.2f} MB")
                logger.info(f"  Final result shape: {final_result.shape}")
                
                # Memory should stay relatively constant
                memory_std = np.std(memory_samples)
                assert memory_std < np.mean(memory_samples) * 0.5
        
        def test_streaming_data_processing(self):
            """Test memory usage with streaming data."""
            import io
            
            with TestMemoryUsage().track_memory("Streaming data processing"):
                # Create large data stream
                stream_size = 100 * 1024 * 1024  # 100MB
                chunk_size = 1024 * 1024  # 1MB chunks
                
                # Simulate stream
                data_stream = io.BytesIO(b'x' * stream_size)
                data_stream.seek(0)
                
                processed_bytes = 0
                memory_samples = []
                
                while True:
                    # Memory before processing chunk
                    before_stats = TestMemoryUsage.MemoryStats.current()
                    
                    # Read chunk
                    chunk = data_stream.read(chunk_size)
                    if not chunk:
                        break
                    
                    # Process chunk (simple hash for demo)
                    chunk_hash = hash(chunk)
                    processed_bytes += len(chunk)
                    
                    # Memory after processing
                    after_stats = TestMemoryUsage.MemoryStats.current()
                    memory_samples.append(after_stats.rss_mb - before_stats.rss_mb)
                    
                    # Clear chunk reference
                    del chunk
                
                logger.info(f"Streaming processing test:")
                logger.info(f"  Stream size: {stream_size / 1024 / 1024:.0f} MB")
                logger.info(f"  Chunk size: {chunk_size / 1024:.0f} KB")
                logger.info(f"  Chunks processed: {len(memory_samples)}")
                logger.info(f"  Total processed: {processed_bytes / 1024 / 1024:.1f} MB")
                logger.info(f"  Average memory delta per chunk: {np.mean(memory_samples):.2f} MB")
                logger.info(f"  Max memory delta: {np.max(memory_samples):.2f} MB")
                
                # Memory should remain stable
                assert np.max(memory_samples) < 10.0  # Less than 10MB increase per chunk
    
    # Test Group 7: Concurrent Memory Usage
    class TestConcurrentMemoryUsage:
        """Tests for concurrent memory usage."""
        
        def test_thread_concurrent_memory_usage(self):
            """Test memory usage with concurrent threads."""
            import concurrent.futures
            
            with TestMemoryUsage().track_memory("Thread concurrent memory usage"):
                def memory_worker(worker_id, memory_per_worker_mb):
                    """Worker that uses memory."""
                    # Allocate memory
                    data = bytearray(memory_per_worker_mb * 1024 * 1024)
                    
                    # Simulate work
                    time.sleep(0.1)
                    
                    # Return memory usage
                    process = psutil.Process()
                    return worker_id, process.memory_info().rss / 1024 / 1024
                
                # Run concurrent workers
                n_workers = 10
                memory_per_worker_mb = 10  # 10MB per worker
                
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
                    futures = []
                    for i in range(n_workers):
                        future = executor.submit(memory_worker, i, memory_per_worker_mb)
                        futures.append(future)
                    
                    # Collect results
                    results = []
                    for future in concurrent.futures.as_completed(futures):
                        worker_id, memory = future.result()
                        results.append((worker_id, memory))
                
                end_stats = TestMemoryUsage.MemoryStats.current()
                total_increase = end_stats.rss_mb - start_stats.rss_mb
                
                logger.info(f"Thread concurrent memory test:")
                logger.info(f"  Workers: {n_workers}")
                logger.info(f"  Memory per worker: {memory_per_worker_mb} MB")
                logger.info(f"  Total expected: {n_workers * memory_per_worker_mb} MB")
                logger.info(f"  Actual increase: {total_increase:.1f} MB")
                logger.info(f"  Efficiency: {(total_increase / (n_workers * memory_per_worker_mb) * 100):.1f}%")
                
                for worker_id, memory in sorted(results):
                    logger.info(f"    Worker {worker_id}: {memory:.1f} MB")
        
        def test_process_concurrent_memory_usage(self):
            """Test memory usage with concurrent processes."""
            import multiprocessing
            
            def process_worker(worker_id, memory_mb, result_queue):
                """Worker process that uses memory."""
                # Allocate memory
                data = bytearray(memory_mb * 1024 * 1024)
                
                # Simulate work
                time.sleep(0.5)
                
                # Get memory usage
                process = psutil.Process()
                memory_used = process.memory_info().rss / 1024 / 1024
                
                # Send result back
                result_queue.put((worker_id, memory_used))
                
                # Keep data alive until process ends
                time.sleep(0.1)
            
            with TestMemoryUsage().track_memory("Process concurrent memory usage"):
                n_processes = 4
                memory_per_process_mb = 50  # 50MB per process
                
                # System memory before
                system_before = psutil.virtual_memory()
                
                # Create processes
                processes = []
                result_queue = multiprocessing.Queue()
                
                for i in range(n_processes):
                    process = multiprocessing.Process(
                        target=process_worker,
                        args=(i, memory_per_process_mb, result_queue)
                    )
                    processes.append(process)
                    process.start()
                
                # Wait for completion
                for process in processes:
                    process.join()
                
                # Collect results
                results = []
                while not result_queue.empty():
                    results.append(result_queue.get())
                
                # System memory after
                system_after = psutil.virtual_memory()
                
                logger.info(f"Process concurrent memory test:")
                logger.info(f"  Processes: {n_processes}")
                logger.info(f"  Memory per process: {memory_per_process_mb} MB")
                logger.info(f"  System memory before: {system_before.used / 1024 / 1024:.0f} MB")
                logger.info(f"  System memory after: {system_after.used / 1024 / 1024:.0f} MB")
                logger.info(f"  System increase: {(system_after.used - system_before.used) / 1024 / 1024:.0f} MB")
                
                for worker_id, memory in sorted(results):
                    logger.info(f"    Process {worker_id}: {memory:.1f} MB")
        
        def test_asyncio_concurrent_memory_usage(self):
            """Test memory usage with concurrent asyncio tasks."""
            async def async_memory_worker(task_id, memory_mb):
                """Async worker that uses memory."""
                # Allocate memory
                data = bytearray(memory_mb * 1024 * 1024)
                
                # Simulate async work
                await asyncio.sleep(0.1)
                
                # Get memory usage
                process = psutil.Process()
                memory_used = process.memory_info().rss / 1024 / 1024
                
                return task_id, memory_used
            
            async def run_concurrent_tasks():
                """Run multiple async memory workers."""
                n_tasks = 20
                memory_per_task_mb = 5  # 5MB per task
                
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                # Create tasks
                tasks = []
                for i in range(n_tasks):
                    task = asyncio.create_task(async_memory_worker(i, memory_per_task_mb))
                    tasks.append(task)
                
                # Wait for all tasks
                results = await asyncio.gather(*tasks)
                
                end_stats = TestMemoryUsage.MemoryStats.current()
                total_increase = end_stats.rss_mb - start_stats.rss_mb
                
                return results, total_increase
            
            with TestMemoryUsage().track_memory("Async concurrent memory usage"):
                results, total_increase = asyncio.run(run_concurrent_tasks())
                
                logger.info(f"Async concurrent memory test:")
                logger.info(f"  Tasks: {len(results)}")
                logger.info(f"  Memory per task: 5 MB")
                logger.info(f"  Total expected: {len(results) * 5} MB")
                logger.info(f"  Actual increase: {total_increase:.1f} MB")
                logger.info(f"  Efficiency: {(total_increase / (len(results) * 5) * 100):.1f}%")
                
                for task_id, memory in sorted(results, key=lambda x: x[0])[:5]:
                    logger.info(f"    Task {task_id}: {memory:.1f} MB")
        
        def test_memory_contention_test(self):
            """Test memory contention under concurrent access."""
            import threading
            import queue
            
            shared_memory = []
            lock = threading.Lock()
            
            def contending_worker(worker_id, iterations, memory_queue):
                """Worker that contends for shared memory."""
                local_memory = []
                
                for i in range(iterations):
                    with lock:
                        # Add to shared memory
                        chunk = bytearray(1024 * 1024)  # 1MB
                        shared_memory.append(chunk)
                    
                    # Also use local memory
                    local_chunk = bytearray(512 * 1024)  # 512KB
                    local_memory.append(local_chunk)
                    
                    # Every 10 iterations, release some
                    if i % 10 == 0 and local_memory:
                        local_memory.pop()
                    
                    # Simulate work
                    time.sleep(0.001)
                
                # Report memory usage
                process = psutil.Process()
                memory_used = process.memory_info().rss / 1024 / 1024
                memory_queue.put((worker_id, memory_used))
            
            with TestMemoryUsage().track_memory("Memory contention test"):
                n_workers = 8
                iterations = 100
                
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                # Start workers
                threads = []
                memory_queue = queue.Queue()
                
                for i in range(n_workers):
                    thread = threading.Thread(
                        target=contending_worker,
                        args=(i, iterations, memory_queue)
                    )
                    threads.append(thread)
                    thread.start()
                
                # Wait for completion
                for thread in threads:
                    thread.join()
                
                # Collect results
                results = []
                while not memory_queue.empty():
                    results.append(memory_queue.get())
                
                end_stats = TestMemoryUsage.MemoryStats.current()
                total_increase = end_stats.rss_mb - start_stats.rss_mb
                
                # Cleanup
                shared_memory.clear()
                gc.collect()
                
                logger.info(f"Memory contention test:")
                logger.info(f"  Workers: {n_workers}")
                logger.info(f"  Iterations per worker: {iterations}")
                logger.info(f"  Shared memory chunks: {len(shared_memory)}")
                logger.info(f"  Total memory increase: {total_increase:.1f} MB")
                
                for worker_id, memory in sorted(results):
                    logger.info(f"    Worker {worker_id}: {memory:.1f} MB")
        
        def test_thread_local_memory_usage(self):
            """Test memory usage with thread-local storage."""
            import threading
            
            # Thread-local storage
            thread_local = threading.local()
            
            def thread_local_worker(worker_id, chunks):
                """Worker using thread-local storage."""
                # Initialize thread-local list
                if not hasattr(thread_local, 'data'):
                    thread_local.data = []
                
                # Add data to thread-local storage
                for i in range(chunks):
                    chunk = bytearray(1024 * 1024)  # 1MB
                    thread_local.data.append(chunk)
                    
                    # Simulate work
                    time.sleep(0.001)
                
                # Get memory usage
                process = psutil.Process()
                return worker_id, process.memory_info().rss / 1024 / 1024
            
            with TestMemoryUsage().track_memory("Thread-local memory usage"):
                n_workers = 5
                chunks_per_worker = 20
                
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                # Run workers sequentially (thread-local persists)
                results = []
                for i in range(n_workers):
                    worker_id, memory = thread_local_worker(i, chunks_per_worker)
                    results.append((worker_id, memory))
                
                end_stats = TestMemoryUsage.MemoryStats.current()
                total_increase = end_stats.rss_mb - start_stats.rss_mb
                
                # Cleanup thread-local
                if hasattr(thread_local, 'data'):
                    del thread_local.data
                gc.collect()
                
                logger.info(f"Thread-local memory test:")
                logger.info(f"  Workers: {n_workers}")
                logger.info(f"  Chunks per worker: {chunks_per_worker}")
                logger.info(f"  Total chunks: {n_workers * chunks_per_worker}")
                logger.info(f"  Total memory increase: {total_increase:.1f} MB")
                
                for worker_id, memory in results:
                    logger.info(f"    After worker {worker_id}: {memory:.1f} MB")
    
    # Test Group 8: Memory Optimization Validation
    class TestMemoryOptimizationValidation:
        """Tests for memory optimization validation."""
        
        def test_object_pool_memory_savings(self, object_pool):
            """Test memory savings from object pooling."""
            with TestMemoryUsage().track_memory("Object pool memory savings"):
                # Without pooling
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                objects_without_pool = []
                for i in range(1000):
                    obj = {'id': i, 'data': 'x' * 1024}
                    objects_without_pool.append(obj)
                
                after_without_pool = TestMemoryUsage.MemoryStats.current()
                memory_without_pool = after_without_pool.rss_mb - start_stats.rss_mb
                
                # Cleanup
                objects_without_pool.clear()
                gc.collect()
                
                # With pooling
                after_cleanup = TestMemoryUsage.MemoryStats.current()
                
                objects_with_pool = []
                for i in range(1000):
                    obj = object_pool.acquire()
                    obj['id'] = i
                    obj['data'] = 'x' * 1024
                    objects_with_pool.append(obj)
                
                after_with_pool = TestMemoryUsage.MemoryStats.current()
                memory_with_pool = after_with_pool.rss_mb - after_cleanup.rss_mb
                
                # Release objects back to pool
                for obj in objects_with_pool:
                    object_pool.release(obj)
                
                pool_stats = object_pool.get_stats()
                
                logger.info(f"Object pool memory savings:")
                logger.info(f"  Objects created: 1000")
                logger.info(f"  Memory without pool: {memory_without_pool:.2f} MB")
                logger.info(f"  Memory with pool: {memory_with_pool:.2f} MB")
                logger.info(f"  Memory saved: {memory_without_pool - memory_with_pool:.2f} MB")
                logger.info(f"  Savings percentage: {((memory_without_pool - memory_with_pool) / memory_without_pool * 100):.1f}%")
                logger.info(f"  Objects actually allocated: {pool_stats['objects_created']}")
                logger.info(f"  Reuse rate: {pool_stats['reuse_rate']:.1%}")
                
                # Pool should use less memory
                assert memory_with_pool < memory_without_pool
        
        def test_memory_view_efficiency(self):
            """Test memory efficiency of memoryview vs slicing."""
            with TestMemoryUsage().track_memory("Memory view efficiency"):
                # Create large bytearray
                data = bytearray(100 * 1024 * 1024)  # 100MB
                
                # Test 1: Slicing (creates copy)
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                slices = []
                for i in range(100):
                    slice_data = data[i*1024*1024:(i+1)*1024*1024]  # 1MB slices
                    slices.append(slice_data)
                
                after_slicing = TestMemoryUsage.MemoryStats.current()
                memory_slicing = after_slicing.rss_mb - start_stats.rss_mb
                
                # Cleanup
                slices.clear()
                gc.collect()
                
                # Test 2: Memoryview (no copy)
                after_cleanup = TestMemoryUsage.MemoryStats.current()
                
                views = []
                mv = memoryview(data)
                for i in range(100):
                    view = mv[i*1024*1024:(i+1)*1024*1024]  # 1MB views
                    views.append(view)
                
                after_views = TestMemoryUsage.MemoryStats.current()
                memory_views = after_views.rss_mb - after_cleanup.rss_mb
                
                logger.info(f"Memory view efficiency:")
                logger.info(f"  Data size: 100 MB")
                logger.info(f"  Slices created: 100")
                logger.info(f"  Memory with slicing (copies): {memory_slicing:.2f} MB")
                logger.info(f"  Memory with memoryview (views): {memory_views:.2f} MB")
                logger.info(f"  Memory saved: {memory_slicing - memory_views:.2f} MB")
                logger.info(f"  Efficiency improvement: {((memory_slicing - memory_views) / memory_slicing * 100):.1f}%")
                
                # Memoryview should use significantly less memory
                assert memory_views < memory_slicing * 0.1  # Less than 10% of slicing memory
        
        def test_generator_memory_efficiency(self):
            """Test memory efficiency of generators vs lists."""
            with TestMemoryUsage().track_memory("Generator memory efficiency"):
                n_items = 1000000
                
                # Test 1: List (stores all items in memory)
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                items_list = []
                for i in range(n_items):
                    items_list.append(f"item_{i}:{'x' * 100}")
                
                after_list = TestMemoryUsage.MemoryStats.current()
                memory_list = after_list.rss_mb - start_stats.rss_mb
                
                # Process list
                list_result = sum(1 for _ in items_list)
                
                # Cleanup
                items_list.clear()
                gc.collect()
                
                # Test 2: Generator (yields items one at a time)
                after_cleanup = TestMemoryUsage.MemoryStats.current()
                
                def items_generator():
                    for i in range(n_items):
                        yield f"item_{i}:{'x' * 100}"
                
                # Process generator
                gen_result = sum(1 for _ in items_generator())
                
                after_generator = TestMemoryUsage.MemoryStats.current()
                memory_generator = after_generator.rss_mb - after_cleanup.rss_mb
                
                logger.info(f"Generator memory efficiency:")
                logger.info(f"  Items: {n_items:,}")
                logger.info(f"  List memory: {memory_list:.2f} MB")
                logger.info(f"  Generator memory: {memory_generator:.2f} MB")
                logger.info(f"  Memory saved: {memory_list - memory_generator:.2f} MB")
                logger.info(f"  Savings: {((memory_list - memory_generator) / memory_list * 100):.1f}%")
                logger.info(f"  List result: {list_result:,}")
                logger.info(f"  Generator result: {gen_result:,}")
                
                # Generator should use much less memory
                assert memory_generator < memory_list * 0.1
        
        def test_slots_memory_optimization(self):
            """Test memory optimization using __slots__."""
            class RegularClass:
                def __init__(self, id, name, value):
                    self.id = id
                    self.name = name
                    self.value = value
            
            class SlotsClass:
                __slots__ = ['id', 'name', 'value']
                
                def __init__(self, id, name, value):
                    self.id = id
                    self.name = name
                    self.value = value
            
            with TestMemoryUsage().track_memory("Slots memory optimization"):
                n_objects = 100000
                
                # Create regular objects
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                regular_objects = []
                for i in range(n_objects):
                    obj = RegularClass(i, f"name_{i}", i * 1.5)
                    regular_objects.append(obj)
                
                after_regular = TestMemoryUsage.MemoryStats.current()
                memory_regular = after_regular.rss_mb - start_stats.rss_mb
                
                # Cleanup
                regular_objects.clear()
                gc.collect()
                
                # Create slots objects
                after_cleanup = TestMemoryUsage.MemoryStats.current()
                
                slots_objects = []
                for i in range(n_objects):
                    obj = SlotsClass(i, f"name_{i}", i * 1.5)
                    slots_objects.append(obj)
                
                after_slots = TestMemoryUsage.MemoryStats.current()
                memory_slots = after_slots.rss_mb - after_cleanup.rss_mb
                
                logger.info(f"Slots memory optimization:")
                logger.info(f"  Objects: {n_objects:,}")
                logger.info(f"  Regular class memory: {memory_regular:.2f} MB")
                logger.info(f"  Slots class memory: {memory_slots:.2f} MB")
                logger.info(f"  Memory saved: {memory_regular - memory_slots:.2f} MB")
                logger.info(f"  Savings: {((memory_regular - memory_slots) / memory_regular * 100):.1f}%")
                
                # Measure object sizes
                from pympler import asizeof
                regular_size = asizeof.asizeof(RegularClass(1, "test", 1.5))
                slots_size = asizeof.asizeof(SlotsClass(1, "test", 1.5))
                
                logger.info(f"  Regular object size: {regular_size} bytes")
                logger.info(f"  Slots object size: {slots_size} bytes")
                logger.info(f"  Per-object savings: {regular_size - slots_size} bytes")
                
                # Slots should use less memory
                assert memory_slots < memory_regular
        
        def test_string_interning_memory(self):
            """Test memory savings from string interning."""
            with TestMemoryUsage().track_memory("String interning memory"):
                n_strings = 100000
                string_pattern = "product_item_description_"
                
                # Without interning
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                strings_without = []
                for i in range(n_strings):
                    # Create new string each time
                    s = f"{string_pattern}{i % 100}"  # Only 100 unique values
                    strings_without.append(s)
                
                after_without = TestMemoryUsage.MemoryStats.current()
                memory_without = after_without.rss_mb - start_stats.rss_mb
                
                # Cleanup
                strings_without.clear()
                gc.collect()
                
                # With interning
                after_cleanup = TestMemoryUsage.MemoryStats.current()
                
                strings_with = []
                for i in range(n_strings):
                    # Use intern to share strings
                    s = sys.intern(f"{string_pattern}{i % 100}")
                    strings_with.append(s)
                
                after_with = TestMemoryUsage.MemoryStats.current()
                memory_with = after_with.rss_mb - after_cleanup.rss_mb
                
                logger.info(f"String interning memory:")
                logger.info(f"  Strings: {n_strings:,}")
                logger.info(f"  Unique values: 100")
                logger.info(f"  Memory without interning: {memory_without:.2f} MB")
                logger.info(f"  Memory with interning: {memory_with:.2f} MB")
                logger.info(f"  Memory saved: {memory_without - memory_with:.2f} MB")
                logger.info(f"  Savings: {((memory_without - memory_with) / memory_without * 100):.1f}%")
                
                # Check string identity
                unique_strings = len({id(s) for s in strings_with})
                logger.info(f"  Unique string objects with interning: {unique_strings:,}")
                
                # Interning should reduce memory
                assert memory_with < memory_without
    
    # Test Group 9: Resource Cleanup Testing
    class TestResourceCleanup:
        """Tests for resource cleanup."""
        
        def test_context_manager_cleanup(self):
            """Test resource cleanup with context managers."""
            
            class Resource:
                def __init__(self, name):
                    self.name = name
                    self.data = bytearray(10 * 1024 * 1024)  # 10MB
                    logger.info(f"  Resource {self.name} allocated")
                
                def close(self):
                    self.data.clear()
                    logger.info(f"  Resource {self.name} cleaned up")
                
                def __enter__(self):
                    return self
                
                def __exit__(self, exc_type, exc_val, exc_tb):
                    self.close()
            
            with TestMemoryUsage().track_memory("Context manager cleanup"):
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                # Use context manager
                with Resource("test_resource") as resource:
                    # Use resource
                    logger.info(f"  Using resource: {resource.name}")
                    memory_during = TestMemoryUsage.MemoryStats.current().rss_mb - start_stats.rss_mb
                    logger.info(f"  Memory during use: {memory_during:.1f} MB")
                
                # After context manager
                end_stats = TestMemoryUsage.MemoryStats.current()
                memory_after = end_stats.rss_mb - start_stats.rss_mb
                
                logger.info(f"Context manager cleanup test:")
                logger.info(f"  Memory increase during: {memory_during:.1f} MB")
                logger.info(f"  Memory increase after: {memory_after:.1f} MB")
                logger.info(f"  Memory reclaimed: {memory_during - memory_after:.1f} MB")
                
                # Context manager should clean up resources
                assert memory_after < memory_during * 0.5  # Most memory reclaimed
        
        def test_finalizer_cleanup_reliability(self):
            """Test reliability of cleanup in finalizers."""
            
            class ResourceWithFinalizer:
                created = 0
                cleaned = 0
                
                def __init__(self, resource_id):
                    self.resource_id = resource_id
                    self.data = bytearray(5 * 1024 * 1024)  # 5MB
                    ResourceWithFinalizer.created += 1
                
                def __del__(self):
                    # Finalizer - cleanup
                    self.data.clear()
                    ResourceWithFinalizer.cleaned += 1
            
            with TestMemoryUsage().track_memory("Finalizer cleanup reliability"):
                # Reset counters
                ResourceWithFinalizer.created = 0
                ResourceWithFinalizer.cleaned = 0
                
                # Create resources
                resources = []
                for i in range(100):
                    resource = ResourceWithFinalizer(i)
                    resources.append(resource)
                
                logger.info(f"  Resources created: {ResourceWithFinalizer.created}")
                
                # Delete references
                resources.clear()
                
                # Try to force cleanup
                for _ in range(3):
                    gc.collect()
                    gc.collect()  # Twice to run finalizers
                
                logger.info(f"  Resources cleaned: {ResourceWithFinalizer.cleaned}")
                logger.info(f"  Cleanup rate: {ResourceWithFinalizer.cleaned / ResourceWithFinalizer.created * 100:.1f}%")
                
                # Note: Finalizers are not guaranteed to run
                # This test demonstrates their unreliability
        
        def test_weakref_cleanup_pattern(self):
            """Test cleanup pattern using weak references."""
            import weakref
            
            class ManagedResource:
                def __init__(self, resource_id):
                    self.resource_id = resource_id
                    self.data = bytearray(10 * 1024 * 1024)  # 10MB
                    logger.info(f"  Resource {resource_id} created")
                
                def cleanup(self):
                    self.data.clear()
                    logger.info(f"  Resource {self.resource_id} manually cleaned")
            
            class ResourceManager:
                def __init__(self):
                    self.resources = weakref.WeakValueDictionary()
                
                def add_resource(self, resource):
                    self.resources[resource.resource_id] = resource
                
                def get_resource(self, resource_id):
                    return self.resources.get(resource_id)
                
                def cleanup_all(self):
                    for resource in list(self.resources.values()):
                        resource.cleanup()
                    self.resources.clear()
            
            with TestMemoryUsage().track_memory("Weakref cleanup pattern"):
                manager = ResourceManager()
                
                # Create resources
                resources = []
                for i in range(10):
                    resource = ManagedResource(i)
                    manager.add_resource(resource)
                    resources.append(resource)  # Strong reference
                
                memory_with_refs = TestMemoryUsage.MemoryStats.current().rss_mb
                
                # Remove strong references
                resources.clear()
                gc.collect()
                
                # Resources should still be in manager (weakref keeps them)
                resource_count = len(manager.resources)
                
                # Manually cleanup
                manager.cleanup_all()
                gc.collect()
                
                memory_after_cleanup = TestMemoryUsage.MemoryStats.current().rss_mb
                
                logger.info(f"Weakref cleanup pattern:")
                logger.info(f"  Resources managed: 10")
                logger.info(f"  Resources after strong ref removal: {resource_count}")
                logger.info(f"  Memory after cleanup: {memory_after_cleanup:.1f} MB")
        
        def test_atexit_cleanup_hooks(self):
            """Test cleanup hooks registered with atexit."""
            import atexit
            
            cleanup_called = []
            
            def cleanup_hook(hook_id):
                cleanup_called.append(hook_id)
                logger.info(f"  Cleanup hook {hook_id} called")
            
            # Register cleanup hooks
            atexit.register(cleanup_hook, "hook_1")
            atexit.register(cleanup_hook, "hook_2")
            
            # Create some data that needs cleanup
            data = bytearray(50 * 1024 * 1024)  # 50MB
            
            logger.info(f"atexit cleanup hooks registered")
            logger.info(f"  Hooks registered: 2")
            logger.info(f"  Data allocated: 50 MB")
            
            # Note: atexit hooks run when program exits
            # We can't test them fully without exiting
            
            # Manually call cleanup for test
            logger.info("Manually calling cleanup hooks for test...")
            cleanup_hook("test_hook")
            
            # Cleanup data
            data.clear()
            gc.collect()
            
            logger.info(f"Cleanup test completed")
        
        def test_circular_reference_cleanup(self):
            """Test cleanup of circular references."""
            
            class Node:
                def __init__(self, node_id):
                    self.node_id = node_id
                    self.data = bytearray(1024 * 1024)  # 1MB
                    self.neighbors = []  # Circular references
                
                def add_neighbor(self, neighbor):
                    self.neighbors.append(neighbor)
                    neighbor.neighbors.append(self)  # Create circular reference
            
            with TestMemoryUsage().track_memory("Circular reference cleanup"):
                # Create circular references
                nodes = []
                for i in range(100):
                    node = Node(i)
                    nodes.append(node)
                
                # Create circular references between nodes
                for i in range(len(nodes) - 1):
                    nodes[i].add_neighbor(nodes[i + 1])
                
                # Close the circle
                nodes[-1].add_neighbor(nodes[0])
                
                memory_with_circles = TestMemoryUsage.MemoryStats.current().rss_mb
                
                # Try to cleanup by breaking references
                for node in nodes:
                    node.neighbors.clear()
                
                # Clear list
                nodes.clear()
                
                # Force GC
                gc.collect()
                
                memory_after_cleanup = TestMemoryUsage.MemoryStats.current().rss_mb
                memory_reclaimed = memory_with_circles - memory_after_cleanup
                
                logger.info(f"Circular reference cleanup:")
                logger.info(f"  Nodes created: 100")
                logger.info(f"  Circular references: 101")
                logger.info(f"  Memory with circles: {memory_with_circles:.1f} MB")
                logger.info(f"  Memory after cleanup: {memory_after_cleanup:.1f} MB")
                logger.info(f"  Memory reclaimed: {memory_reclaimed:.1f} MB")
                
                # Should reclaim most memory after breaking circles
                assert memory_reclaimed > 50.0  # Should reclaim most of the 100MB
    
    # Test Group 10: Memory Profiling
    class TestMemoryProfiling:
        """Tests for memory profiling."""
        
        def test_tracemalloc_profiling(self):
            """Test memory profiling with tracemalloc."""
            with TestMemoryUsage().tracemalloc_context():
                # Allocate memory in different ways
                data_structures = []
                
                # List of lists
                for i in range(100):
                    inner_list = [f"string_{j}" * 10 for j in range(100)]
                    data_structures.append(inner_list)
                
                # Dictionary
                large_dict = {f"key_{i}": f"value_{i}" * 100 for i in range(1000)}
                data_structures.append(large_dict)
                
                # Nested structure
                nested = {
                    'level1': {
                        'level2': {
                            'level3': [{'data': 'x' * 100} for _ in range(100)]
                        }
                    }
                }
                data_structures.append(nested)
                
                # Take snapshot
                snapshot = tracemalloc.take_snapshot()
                
                # Analyze
                top_stats = snapshot.statistics('lineno')
                
                logger.info(f"tracemalloc profiling:")
                logger.info(f"  Total allocated: {snapshot.statistics('traceback')[0].size / 1024 / 1024:.1f} MB")
                logger.info(f"  Top 5 memory consumers:")
                
                for stat in top_stats[:5]:
                    logger.info(f"    {stat.size / 1024:.1f} KB: {stat.traceback.format()[-1]}")
                
                # Cleanup
                data_structures.clear()
                gc.collect()
        
        def test_pympler_memory_analysis(self):
            """Test memory analysis with Pympler."""
            from pympler import classtracker, muppy, summary
            
            with TestMemoryUsage().track_memory("Pympler memory analysis"):
                # Start class tracker
                tracker = classtracker.ClassTracker()
                tracker.track_class(dict)
                tracker.track_class(list)
                tracker.track_class(str)
                
                tracker.create_snapshot('initial')
                
                # Create objects
                objects = []
                for i in range(1000):
                    obj = {
                        'id': i,
                        'data': 'x' * 100,
                        'list': [j for j in range(10)],
                        'nested': {'key': 'value' * 10}
                    }
                    objects.append(obj)
                
                tracker.create_snapshot('after_creation')
                
                # Get statistics between snapshots
                stats = tracker.stats
                tracker.print_summary()
                
                # Get all objects in memory
                all_objects = muppy.get_objects()
                sum1 = summary.summarize(all_objects)
                
                logger.info(f"Pympler analysis:")
                logger.info(f"  Total objects in memory: {len(all_objects):,}")
                
                # Show top types
                logger.info(f"  Top object types:")
                for entry in sum1[:5]:
                    logger.info(f"    {entry[0]}: {entry[1]:,} objects, {entry[2] / 1024 / 1024:.1f} MB")
                
                # Cleanup
                objects.clear()
                gc.collect()
        
        def test_memory_profiler_line_by_line(self):
            """Test line-by-line memory profiling."""
            # Note: memory_profiler is an external package
            # This test demonstrates the pattern
            
            def memory_intensive_function():
                """Function that uses memory in different lines."""
                # Line 1: Create large list
                large_list = [i * 2 for i in range(100000)]  # ~0.8MB
                
                # Line 2: Create dictionary
                large_dict = {str(i): i * 3 for i in range(10000)}  # ~0.6MB
                
                # Line 3: Create string
                large_string = 'x' * 1000000  # ~1MB
                
                # Line 4: Process
                result = sum(large_list) + sum(large_dict.values()) + len(large_string)
                
                return result
            
            with TestMemoryUsage().track_memory("Line-by-line memory profiling"):
                # Run function
                result = memory_intensive_function()
                
                logger.info(f"Memory intensive function result: {result}")
                logger.info(f"Note: For line-by-line profiling, use @profile decorator")
                logger.info(f"  from memory_profiler import profile")
                logger.info(f"  @profile")
                logger.info(f"  def your_function(): ...")
        
        def test_object_size_analysis(self):
            """Test analysis of individual object sizes."""
            from pympler import asizeof
            
            with TestMemoryUsage().track_memory("Object size analysis"):
                # Test different data structures
                test_objects = []
                
                # Simple types
                test_objects.append(('integer', 42))
                test_objects.append(('float', 3.14159))
                test_objects.append(('string_small', 'hello'))
                test_objects.append(('string_large', 'x' * 1000))
                
                # Collections
                test_objects.append(('list_small', [1, 2, 3]))
                test_objects.append(('list_large', list(range(1000))))
                
                # Dictionary
                test_objects.append(('dict_small', {'a': 1, 'b': 2}))
                test_objects.append(('dict_large', {str(i): i for i in range(1000)}))
                
                # Nested structure
                nested = {
                    'metadata': {'id': 1, 'name': 'test'},
                    'data': [[i * j for j in range(10)] for i in range(100)],
                    'description': 'A nested data structure' * 10
                }
                test_objects.append(('nested_structure', nested))
                
                # Class instance
                class TestClass:
                    def __init__(self):
                        self.id = 1
                        self.name = 'test'
                        self.data = [i for i in range(100)]
                        self.metadata = {'created': datetime.now()}
                
                test_objects.append(('class_instance', TestClass()))
                
                # Analyze sizes
                logger.info(f"Object size analysis:")
                for name, obj in test_objects:
                    size = asizeof.asizeof(obj)
                    logger.info(f"  {name:20s}: {size:8,d} bytes ({size / 1024:6.1f} KB)")
        
        def test_memory_usage_by_component(self):
            """Test memory usage breakdown by component."""
            from microagents.agents.factory import AgentFactory
            from microagents.dsl.compiler import DSLCompiler
            
            with TestMemoryUsage().track_memory("Memory by component"):
                components = {}
                
                # DSL Compiler
                start_stats = TestMemoryUsage.MemoryStats.current()
                
                compiler = DSLCompiler()
                components['DSLCompiler'] = TestMemoryUsage.MemoryStats.current().rss_mb - start_stats.rss_mb
                
                # Agent Factory
                factory_start = TestMemoryUsage.MemoryStats.current()
                
                factory = AgentFactory()
                components['AgentFactory'] = TestMemoryUsage.MemoryStats.current().rss_mb - factory_start.rss_mb
                
                # Create agents
                agents_start = TestMemoryUsage.MemoryStats.current()
                
                agents = []
                for i in range(10):
                    agent = TestMemoryUsage.MemoryIntensiveAgent(
                        agent_id=f"test_agent_{i}",
                        name=f"Test Agent {i}",
                        version="1.0.0"
                    )
                    agents.append(agent)
                
                components['10_Agents'] = TestMemoryUsage.MemoryStats.current().rss_mb - agents_start.rss_mb
                
                # Execute agents
                execution_start = TestMemoryUsage.MemoryStats.current()
                
                for agent in agents:
                    asyncio.run(agent.execute({'array_size': 100}, {}))
                
                components['Agent_Execution'] = TestMemoryUsage.MemoryStats.current().rss_mb - execution_start.rss_mb
                
                # Cleanup
                for agent in agents:
                    agent.cleanup()
                agents.clear()
                gc.collect()
                
                logger.info(f"Memory usage by component:")
                total = sum(components.values())
                for component, memory in components.items():
                    percentage = (memory / total * 100) if total > 0 else 0
                    logger.info(f"  {component:20s}: {memory:6.1f} MB ({percentage:5.1f}%)")
                
                logger.info(f"  {'Total':20s}: {total:6.1f} MB")


# Performance test markers
pytest.mark.performance = pytest.mark.skipif(
    os.getenv("RUN_PERFORMANCE_TESTS", "false").lower() != "true",
    reason="Performance tests disabled by default"
)

# Memory-intensive test markers
pytest.mark.memory_intensive = pytest.mark.skipif(
    os.getenv("RUN_MEMORY_INTENSIVE_TESTS", "false").lower() != "true",
    reason="Memory-intensive tests disabled by default"
)


if __name__ == "__main__":
    # Run specific test groups
    pytest.main([
        __file__,
        "-v",
        "--tb=short",
        "-k", "TestMemoryLeakDetection or TestGarbageCollectionAnalysis",
        "--log-level=INFO"
    ])