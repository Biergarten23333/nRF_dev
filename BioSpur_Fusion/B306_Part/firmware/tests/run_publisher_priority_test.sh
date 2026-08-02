#!/bin/sh
set -eu

cc -std=c11 -Wall -Wextra -Werror \
	-I"$(dirname "$0")/../src" \
	"$(dirname "$0")/test_publisher_priority.c" \
	-o /tmp/biospur_test_publisher_priority
/tmp/biospur_test_publisher_priority
