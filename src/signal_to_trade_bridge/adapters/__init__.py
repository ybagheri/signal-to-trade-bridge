"""Concrete integrations with external systems.

Each sub-package isolates one external dependency, and each is imported only by
the application layer's composition root -- never by the domain. Keeping them in
separate sub-packages rather than one flat module is what makes "which upstream
change breaks what" a question with a short answer.

* ``albrooks`` -- the price-action engine, as a signal source
* ``auto_trade`` -- the execution project, as a trade executor
* ``mt5`` -- MetaTrader 5 account and symbol data
* ``fake`` -- deterministic in-memory doubles, used by the tests

Every module here is optional to import. A machine without MetaTrader 5 bindings
can still import the domain, the application layer and the fakes, and can run the
entire unit test suite.
"""
