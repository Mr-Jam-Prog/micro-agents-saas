import re
import sys

def fix_file(filename):
    with open(filename, 'r') as f:
        content = f.read()

    # Update PR notification script to use process.env safely
    search_pattern = r'uses: actions/github-script@v7\s+with:\s+script: \|\s+const \{ data: checks \} = await github\.rest\.checks\.listForRef\(\{\s+owner: context\.repo\.owner,\s+repo: context\.repo\.repo,\s+ref: context\.payload\.pull_request\.head\.sha\s+\}\);'

    # This is complex to do with regex. Let's look for the specific block.
    if 'await github.rest.issues.createComment' in content:
        # Just replace the whole notify job or specific steps if possible
        pass

if __name__ == "__main__":
    # Actually I'll just use replace_with_git_merge_diff for ci.yml (already done)
    # and check if cd.yml needs it
    pass
