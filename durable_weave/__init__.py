"""durable-weave: durable execution (идея Temporal) + CAS-доказуемость (идея WEAVE/Prolepsis)."""
from .cas import ContentAddressedStore
from .executor import DurableExecutor, ExecutionResult, WorkflowWaiting, SimulatedCrash
from .saga import Saga

__all__ = ["ContentAddressedStore", "DurableExecutor", "ExecutionResult", "WorkflowWaiting", "SimulatedCrash", "Saga"]
__version__ = "0.1.0"
