#!/usr/bin/env python3

import sys
import os


def set_range(path, degrees):

    try:

        with open(path, "w") as f:

            f.write(str(int(degrees)))

        return True

    except Exception as e:

        print(e)

        return False


def main():

    if len(sys.argv) != 3:

        print(
            "Usage: wheel_helper.py <range_file> <degrees>"
        )

        sys.exit(1)

    path = sys.argv[1]

    degrees = sys.argv[2]

    ok = set_range(
        path,
        degrees
    )

    sys.exit(
        0 if ok else 1
    )


if __name__ == "__main__":

    main()
