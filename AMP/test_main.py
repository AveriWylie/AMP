"""
--------------------------------------------------------------------------------------------
Test Main
--------------------------------------------------------------------------------------------
Test all functions within this directory by starting the specific tests. Unit tester file.
A file need not be named main to have the main, it only tells the start of this program.

Without this, a python script runs top to bottom, in this way we may test functionality
within later statements but that is not done dynamically at run time as we need.
--------------------------------------------------------------------------------------------
"""

import connection_test as con_test

def main():
    con_test.run()

if __name__ == "__main__":
    main()
