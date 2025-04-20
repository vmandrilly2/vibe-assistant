# Copyright 2023-2024 Deepgram SDK contributors. All Rights Reserved.
# Use of this source code is governed by a MIT license that can be found in the LICENSE file.
# SPDX-License-Identifier: MIT

import os
import sys
import logging
from deepgram.utils import verboselogs
from dotenv import load_dotenv

from deepgram import DeepgramClient, DeepgramClientOptions, KeyOptions

load_dotenv()


def main():
    try:
        # example of setting up a client config. logging values: WARNING, VERBOSE, DEBUG, SPAM
        config = DeepgramClientOptions(
            verbose=verboselogs.SPAM,
        )
        deepgram: DeepgramClient = DeepgramClient("", config)
        # otherwise, use default config
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

        # list keys
        listResp = deepgram.manage.v("1").get_keys(myId)
        if listResp is None:
            print("No keys found")
        else:
            for key in listResp.api_keys:
                print(
                    f"GetKeys() - ID: {key.api_key.api_key_id}, Member: {key.member.email}, Comment: {key.api_key.comment}, Scope: {key.api_key.scopes}"
                )

        # create key
        options: KeyOptions = KeyOptions(
            comment="MyTestKey",
            scopes=["member:write", "project:read"],
            time_to_live_in_seconds=3600,
        )

        myKeyId = None
        createResp = deepgram.manage.v("1").create_key(myId, options)
        if createResp is None:
            print(f"CreateKey failed.")
            sys.exit(1)
        else:
            myKeyId = createResp.api_key_id
            print(
                f"CreateKey() - ID: {myKeyId}, Comment: {createResp.comment} Scope: {createResp.scopes}"
            )

        # list keys
        listResp = deepgram.manage.v("1").get_keys(myId)
        if listResp is None:
            print("No keys found")
        else:
            for key in listResp.api_keys:
                print(
                    f"GetKeys() - ID: {key.api_key.api_key_id}, Member: {key.member.email}, Comment: {key.api_key.comment}, Scope: {key.api_key.scopes}"
                )

        # get key
        getResp = deepgram.manage.v("1").get_key(myId, myKeyId)
        if getResp is None:
            print(f"GetKey failed.")
            sys.exit(1)
        else:
            print(
                f"GetKey() - ID: {key.api_key.api_key_id}, Member: {key.member.email}, Comment: {key.api_key.comment}, Scope: {key.api_key.scopes}"
            )

        # delete key
        deleteResp = deepgram.manage.v("1").delete_key(myId, myKeyId)
        if deleteResp is None:
            print(f"DeleteKey failed.")
            sys.exit(1)
        else:
            print(f"DeleteKey() - Msg: {deleteResp.message}")

        # list keys
        listResp = deepgram.manage.v("1").get_keys(myId)
        if listResp is None:
            print("No keys found")
        else:
            for key in listResp.api_keys:
                print(
                    f"GetKeys() - ID: {key.api_key.api_key_id}, Member: {key.member.email}, Comment: {key.api_key.comment}, Scope: {key.api_key.scopes}"
                )
    except Exception as e:
        print(f"Exception: {e}")


if __name__ == "__main__":
    main()
