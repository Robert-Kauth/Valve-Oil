"""Where each printer's toolhead can go, for the tests that check moves against it."""

# Toward the back, each printer's toolhead fits only between narrowing X limits: (behind Y, lowest X, highest X).
REAR_LIMITS = {
    # Behind Y384 the Max 4's toolhead clears the left motor mount from X54 and the right one up to X337; behind Y399
    # its cable reaches the cable chain mount past X189 (operator measurement, 2026-11-23).
    "max4": ((384, 54, 337), (399, 54, 189)),
    # Behind Y320 the Plus 5's toolhead can only be between X75 and X231 (operator, 2026-11-11).
    "plus5": ((320, 75, 231),),
    # Behind the Smart 5's bed its toolhead has to stay between X66 and X100 (operator).
    "smart5": ((180, 66, 100),),
}
TRAVEL = {"plus5": {"X": (-7, 326), "Y": (-5, 340)}, "smart5": {"X": (-2, 185), "Y": (-4, 200)}}
