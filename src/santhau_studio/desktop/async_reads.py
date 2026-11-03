"""Worker-thread read boundary. Every request owns its SQLite connections."""
import json

from PySide6.QtCore import QObject, QRunnable, Signal

READS = {
    'onboarding', 'control_center', 'sources', 'jobs', 'coverage', 'search',
    'record_detail', 'record_history', 'raw_object', 'deep_queue', 'integrity', 'settings',
    'job_status', 'coverage_proof', 'start_worker', 'worker_command', 'enqueue_collection', 'start_collection', 'enqueue_deep',
}


class ReadSignals(QObject):
    completed = Signal(str, int, int, str)


class ReadTask(QRunnable):
    def __init__(self, backend, key, serial, generation, method, payload):
        super().__init__()
        self.backend, self.key, self.serial, self.generation = backend, key, serial, generation
        self.method, self.payload = method, payload
        self.signals = ReadSignals()

    def run(self):
        try:
            if self.method not in READS:
                raise ValueError('Unknown read operation')
            if self.method == 'start_worker':
                from santhau_studio.desktop.supervisor import WorkerSupervisor
                method = WorkerSupervisor(self.backend.data_root).ensure_running
            else:
                method = getattr(self.backend, self.method)
            args = ()
            if self.method in ('search', 'record_history', 'enqueue_collection', 'start_collection', 'enqueue_deep'):
                args = (self.payload,)
            elif self.method == 'worker_command':
                args = (self.payload.get('command'), self.payload.get('job_id') or None)
            elif self.method == 'coverage':
                args = (self.payload.get('family') or None,)
            elif self.method == 'coverage_proof':
                args = (self.payload.get('proof_id'),)
            elif self.method == 'record_detail':
                args = (int(self.payload.get('record_id', 0)),)
            elif self.method == 'raw_object':
                args = (str(self.payload.get('sha', '')),)
            elif self.method == 'job_status':
                args = (str(self.payload.get('job_id', '')),)
            result = method(*args)
        except Exception as exc:
            from santhau_studio.collectors.network_policy import scrub_exception
            result = {'error': scrub_exception(exc), 'error_code': 'READ_FAILED'}
        self.signals.completed.emit(self.key, self.serial, self.generation,
                                    json.dumps(result, ensure_ascii=False, default=str))
