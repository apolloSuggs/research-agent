"""errors.py: the error standard every tool follows (exam task 2.2), plus the bounded retry (task 5.3).

Rule: contain, then report. A tool never lets a raw exception escape. It returns ONE structured error:
    errorCategory  what kind of failure (see CATEGORIES)
    isRetryable    could trying again help?  Only transient errors say yes.
    message        plain English for the agent
    attempted      what was tried, e.g. "get_account_details(customer_id=CUST-1001)"
    attempts       how many tries were made

"Not found" is NOT here on purpose. "No account with that ID" is a valid answer to the question,
not a failure to ask it. Tools return it as a normal result with found: false.
"""

import time

CATEGORIES = {
    "transient": "A temporary problem (timeout, service busy). Trying again may work.",
    "validation": "The input was malformed. Fix the input; retrying the same input will fail again.",
    "business": "The request breaks a business rule. Retrying will not change the rule.",
    "permission": "The caller is not allowed to see this. Retrying will not change that.",
    "internal": "A bug in the tool itself. Retrying will not help; it needs a fix.",
}


class ToolError(Exception):
    """Raised INSIDE a tool, caught by the tool runner, turned into an error result. Never leaves the tool."""

    def __init__(self, category, message):
        assert category in CATEGORIES, category
        super().__init__(message)
        self.category = category
        self.message = message
        self.attempts = 1

    @property
    def retryable(self):
        return self.category == "transient"


def error_result(err, attempted):
    return {
        "isError": True,
        "errorCategory": err.category,
        "isRetryable": err.retryable,
        "message": err.message,
        "attempted": attempted,
        "attempts": err.attempts,
    }


def with_retry(fn, max_retries=2, base_delay=0.2, sleep=time.sleep):
    """Call fn(attempt_number). Retry ONLY transient errors, up to max_retries more times, waiting
    base_delay, then 2x, 4x... (exponential backoff). Returns (value, attempts).
    Any other error is raised at once: retrying bad input or a permission denial cannot help."""
    attempt = 1
    while True:
        try:
            return fn(attempt), attempt
        except ToolError as err:
            err.attempts = attempt
            if not err.retryable or attempt > max_retries:
                raise
            sleep(base_delay * 2 ** (attempt - 1))
            attempt += 1
