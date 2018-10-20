from enum import Enum

from .client import SelfPostingClient
from .etheno import EthenoPlugin

class DifferentialTest(object):
    def __init__(self, test_name, success, message = ''):
        self.test_name = test_name
        self.message = message
        self.success = success
    def __str__(self):
        return "[%s] %s\t%s" % (self.test_name, self.success, self.message)
    __repr__ = __str__

class TestResult(Enum):
    FAILED = 0
    PASSED = 1

class DifferentialTester(EthenoPlugin):
    def __init__(self):
        self._unprocessed_transactions = set()
        self.tests = {}
        self._printed_summary = False

    def add_test_result(self, result):
        if result.test_name not in self.tests:
            self.tests[result.test_name] = {}
        if result.success not in self.tests[result.test_name]:
            self.tests[result.test_name][result.success] = []
        self.tests[result.test_name][result.success].append(result)

    def after_post(self, data, client_results):
        method = data['method']
        if method == 'eth_sendTransaction' or method == 'eth_sendRawTransaction':
            if 'result' in data and data['result']:
                self._unprocessed_transactions.add(data['result'])
        elif method == 'eth_getTransactionReceipt':
            master_result = client_results[0]
            if master_result and 'result' in master_result and master_result['result']:
                # mark that we have processed the receipt for this transaction:
                if data['params'][0] in self._unprocessed_transactions:
                    self._unprocessed_transactions.remove(data['params'][0])

                if 'contractAddress' in master_result['result'] and master_result['result']['contractAddress']:
                    # the master client created a new contract
                    # so make sure that all of the other clients did, too
                    for client, client_data in zip(self.etheno.clients, client_results[1:]):
                        created = False
                        try:
                            created = client_data['result']['contractAddress']
                        except Exception:
                            pass
                        if not created:
                            test = DifferentialTest('CONTRACT_CREATION', TestResult.FAILED, "the master client created a contract for transaction %s, but %s did not" % (data['params'][0], client))
                            self.add_test_result(test)
                            print("Error: %s!" % test.message)
                        else:
                            self.add_test_result(DifferentialTest('CONTRACT_CREATION', TestResult.PASSED,  "client %s transaction %s" % (client, data['params'][0])))
                if 'gasUsed' in master_result['result'] and master_result['result']['gasUsed']:
                    # make sure each client used the same amount of gas
                    master_gas = int(master_result['result']['gasUsed'], 16)
                    for client, client_data in zip(self.etheno.clients, client_results[1:]):
                        gas_used = 0
                        try:
                            gas_used = int(client_data['result']['gasUsed'], 16)
                        except Exception:
                            pass
                        if gas_used != master_gas:
                            test = DifferentialTest('GAS_USAGE', TestResult.FAILED, "transaction %s used 0x%x gas in the master client but only 0x%x gas in %s!" % (data['params'][0], master_gas, gas_used, client))
                            self.add_test_result(test)
                            print("Error: %s" % test.message)
                        else:
                            self.add_test_result(DifferentialTest('GAS_USAGE', TestResult.PASSED, "client %s transaction %s used 0x%x gas" % (client, data['params'][0], gas_used)))

    def finalize(self):
        unprocessed = self._unprocessed_transactions
        self._unprocessed_transactions = set()
        for tx_hash in unprocessed:
            print("Requesting transaction receipt for %s to check differentials..." % tx_hash)
            if not isinstance(self.etheno.master_client, SelfPopstingClient):
                print("Warning: The DifferentialTester currently only supports master clients that extend from SelfPostingClient, but %s does not; skipping checking transaction(s) %s" % (self.etheno.master_client, ', '.join(unprocessed)))
                return
            while True:
                receipt = self.etheno.post({
                    'jsonrpc': '2.0',
                    'method': 'eth_getTransactionReceipt',
                    'params': [tx_hash]
                })
                # if this post is successful, it will trigger the `after_post` callback above
                # where were check for the differentials
                if 'result' in receipt and receipt['result']:
                    break
                # The transaction is still pending
                time.sleep(3.0)

    def shutdown(self):
        if self.tests and not self._printed_summary:
            self._printed_summary = True
            print("\nDifferential Test Summary:\n")
            for test in self.tests:
                print("    %s" % test)
                total = sum(map(len, self.tests[test].values()))
                for result in self.tests[test]:
                    print("        %s\t%d / %d" % (result, len(self.tests[test][result]), total))
                print('')
        super().shutdown()
