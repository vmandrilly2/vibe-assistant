# Copyright 2023-2024 Deepgram SDK contributors. All Rights Reserved.
# Use of this source code is governed by a MIT license that can be found in the LICENSE file.
# SPDX-License-Identifier: MIT

import os
import sys
from dotenv import load_dotenv

from deepgram import DeepgramClient, ScopeOptions

load_dotenv()

# environment variables
MEMBER_BY_EMAIL = "enter-your-email@gmail.com"


def main():
    try:
        # Create a Deepgram client using the API key
        deepgram: DeepgramClient = DeepgramClient()

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

        # list members
        memberId = None
        listResp = deepgram.manage.v("1").get_members(myId)
        if listResp is None:
            print("No members found")
        else:
            for member in listResp.members:
                if member.email == MEMBER_BY_EMAIL:
                    memberId = member.member_id
                print(f"GetMembers() - ID: {member.member_id}, Email: {member.email}")

        if memberId == None:
            print(
                'This example requires a member who is already a member with email in the value of "MEMBER_BY_EMAIL".'
            )
            print("This is required to exercise the UpdateMemberScope function.")
            print("In the absence of this, this example will exit early.")
            sys.exit(1)

        # get member scope
        memberResp = deepgram.manage.v("1").get_member_scopes(myId, memberId)
        if memberResp is None:
            print("No scopes found")
            sys.exit(1)
        print(
            f"GetMemberScope() - ID: {myId}, Email: {memberId}, Scope: {memberResp.scopes}"
        )

        # update scope
        options: ScopeOptions = {"scope": "admin"}

        updateResp = deepgram.manage.v("1").update_member_scope(myId, memberId, options)
        print(f"UpdateMemberScope() - Msg: {updateResp.message}")

        # get member scope
        memberResp = deepgram.manage.v("1").get_member_scopes(myId, memberId)
        if memberResp is None:
            print("No scopes found")
            sys.exit(1)
        print(
            f"GetMemberScope() - ID: {myId}, Email: {memberId}, Scope: {memberResp.scopes}"
        )

        # update scope
        options: ScopeOptions = {"scope": "member"}

        updateResp = deepgram.manage.v("1").update_member_scope(myId, memberId, options)
        print(f"UpdateMemberScope() - Msg: {updateResp.message}")

        # get member scope
        memberResp = deepgram.manage.v("1").get_member_scopes(myId, memberId)
        if memberResp is None:
            print("No scopes found")
            sys.exit(1)
        print(
            f"GetMemberScope() - ID: {myId}, Email: {memberId}, Scope: {memberResp.scopes}"
        )
    except Exception as e:
        print(f"Exception: {e}")


if __name__ == "__main__":
    main()
