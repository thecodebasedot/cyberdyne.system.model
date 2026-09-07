---------------------------- MODULE SafetyGate ----------------------------
(* The system state machine and the safety gate of the Cyberdyne System   *)
(* Model, as checked exhaustively by cyberdyne/safety/verify.py.          *)
(* Run with TLC using SafetyGate.cfg.                                     *)
EXTENDS Naturals

CONSTANTS MaxLin, MaxAng, StopDist

VARIABLES state, estop, battCritical, charging, stale, clearance,
          reqLin, reqAng, appLin, appAng

States == {"boot", "diagnostic", "idle", "active", "charging", "estop", "shutdown"}

Trans == [boot       |-> {"diagnostic", "shutdown"},
          diagnostic |-> {"idle", "estop", "shutdown"},
          idle       |-> {"active", "charging", "estop", "shutdown"},
          active     |-> {"idle", "charging", "estop", "shutdown"},
          charging   |-> {"idle", "active", "estop", "shutdown"},
          estop      |-> {"diagnostic", "shutdown"},
          shutdown   |-> {}]

Lin == -2*MaxLin..2*MaxLin
Ang == -2*MaxAng..2*MaxAng
Clr == 0..(StopDist+2)

Clamp(x, m) == IF x > m THEN m ELSE IF x < -m THEN -m ELSE x

Gate(rl, ra, es, bc, ch, st, c) ==
  LET l0 == IF es THEN 0 ELSE rl
      a0 == IF es THEN 0 ELSE ra
      l1 == IF bc /\ ~ch /\ l0 > 0 THEN 0 ELSE l0
      l2 == Clamp(l1, MaxLin)
      a2 == Clamp(a0, MaxAng)
      l3 == IF c < StopDist /\ l2 > 0 THEN 0 ELSE l2
      l4 == IF st /\ l3 > 0 THEN 0 ELSE l3
  IN <<l4, a2>>

Init == /\ state = "boot" /\ estop = FALSE /\ battCritical = FALSE /\ charging = FALSE
        /\ stale = FALSE /\ clearance = StopDist + 2 /\ reqLin = 0 /\ reqAng = 0
        /\ appLin = 0 /\ appAng = 0

Environment ==
  /\ estop' \in BOOLEAN /\ battCritical' \in BOOLEAN /\ charging' \in BOOLEAN
  /\ stale' \in BOOLEAN /\ clearance' \in Clr /\ reqLin' \in Lin /\ reqAng' \in Ang

Step ==
  /\ state' \in Trans[state] \cup {state}
  /\ (state' = "estop") => estop'
  /\ Environment
  /\ <<appLin', appAng'>> = Gate(reqLin', reqAng', estop', battCritical', charging', stale', clearance')

Next == Step

Spec == Init /\ [][Next]_<<state, estop, battCritical, charging, stale, clearance, reqLin, reqAng, appLin, appAng>>

(* Invariants I1..I6 *)
EstopStops        == estop => (appLin = 0 /\ appAng = 0)
BatteryNoForward  == (battCritical /\ ~charging) => appLin <= 0
StaleNoForward    == stale => appLin <= 0
ObstacleNoForward == (clearance < StopDist) => appLin <= 0
Envelope          == appLin <= MaxLin /\ appLin >= -MaxLin /\ appAng <= MaxAng /\ appAng >= -MaxAng
EstopExitsOnly    == Trans["estop"] \subseteq {"diagnostic", "shutdown"} /\ Trans["shutdown"] = {}

Safe == EstopStops /\ BatteryNoForward /\ StaleNoForward /\ ObstacleNoForward /\ Envelope /\ EstopExitsOnly
=============================================================================
