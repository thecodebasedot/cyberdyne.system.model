"""Learning layer (Phase 4).

    interfaces   Experience / ReplayBuffer / Learner contracts
    user_model   habits per person -> proactive suggestions
    recorder     learning from demonstration (routes -> tasks)
    tuner        shielded parameter search for the local controller (offline, in sim)
"""
from .interfaces import Experience, Learner, ReplayBuffer
from .recorder import DemoRecorder
from .tuner import ControllerTuner, TuneResult
from .user_model import UserModel, UserModelModule

__all__ = ["Experience", "Learner", "ReplayBuffer", "DemoRecorder", "ControllerTuner", "TuneResult",
           "UserModel", "UserModelModule"]
