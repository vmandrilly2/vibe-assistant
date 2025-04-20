# Copyright 2023-2024 Deepgram SDK contributors. All Rights Reserved.
# Use of this source code is governed by a MIT license that can be found in the LICENSE file.
# SPDX-License-Identifier: MIT

import os
import sys
from dotenv import load_dotenv
import logging
from deepgram.utils import verboselogs

from deepgram import DeepgramClient, DeepgramClientOptions

load_dotenv()


def main():
    try:
        # STEP 1 Create a Deepgram client using the API key in the environment variables
        config: DeepgramClientOptions = DeepgramClientOptions(
            verbose=verboselogs.SPAM,
        )
        deepgram: DeepgramClient = DeepgramClient("", config)
        # OR use defaults
        # deepgram: DeepgramClient = DeepgramClient()

        # get projects
        projectResp = deepgram.manage.v("1").get_projects()
        if projectResp is None:
            print(f"ListProjects failed.")
            sys.exit(1)

        myId = None
        myName = None
        for project in projectResp.projects:
            myId = project.project_id
            myName = project.name
            print(f"ListProjects() - ID: {myId}, Name: {myName}")
            break

        # list balances
        listResp = deepgram.manage.v("1").get_balances(myId)
        if listResp is None:
            print(f"ListBalances failed.")
            sys.exit(1)

        myBalanceId = None
        for balance in listResp.balances:
            myBalanceId = balance.balance_id
            print(
                f"GetBalance() - Name: {balance.balance_id}, Amount: {balance.amount}"
            )

        # get balance
        getResp = deepgram.manage.v("1").get_balance(myId, myBalanceId)
        print(f"GetBalance() - Name: {getResp.balance_id}, Amount: {getResp.amount}")
    except Exception as e:
        print(f"Exception: {e}")


if __name__ == "__main__":
    main()
