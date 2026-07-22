"""
--------------------------------------------------------------------------------------------
Test Main
--------------------------------------------------------------------------------------------
Test all functions within this directory by starting the specific tests. Unit tester file.
A file need not be named main to have the main, it only tells the start of this program.

Without this, a python script runs top to bottom, in this way we may test functionality
within later statements but that is not done dynamically at run time as we need.

This was written at star of project when it was my first time using python in a year lol.
BAsic python comments the reader can ignore.
--------------------------------------------------------------------------------------------
"""

import connection_test as con_test
import iteration01_test as iter_test

def main():
    con_test.run()
    iter_test.run()

if __name__ == "__main__":
    main()
