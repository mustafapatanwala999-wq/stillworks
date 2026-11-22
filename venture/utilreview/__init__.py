"""utilreview: check AI-assisted health-plan utilization-review decisions against state rules.

Input is decision metadata only (no patient identifiers). Output is a list of
adverse decisions lacking the human-review record a state rule calls for, and the
quarterly counts a state report asks for. All rules are data in rules.json and are
unverified; see the README before relying on any output.
"""
