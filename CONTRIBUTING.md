# Contributing Guidelines

Thank you for your interest in contributing to our project. Whether it's a bug report, new feature, correction, or additional
documentation, we greatly value feedback and contributions from our community.

Please read through this document before submitting any issues or pull requests to ensure we have all the necessary
information to effectively respond to your bug report or contribution.


## Reporting Bugs/Feature Requests

We welcome you to use the GitHub issue tracker to report bugs or suggest features.

When filing an issue, please check existing open, or recently closed, issues to make sure somebody else hasn't already
reported the issue. Please try to include as much information as you can. Details like these are incredibly useful:

* A reproducible test case or series of steps
* The version of our code being used
* Any modifications you've made relevant to the bug
* Anything unusual about your environment or deployment


## Elva and upstream branches

In `elva-labs/virtual-engineering-workbench`, `main` is Elva's default working branch, including Elva's changes.
`awslabs` preserves the exact commit history of [official upstream `main`](https://github.com/awslabs/virtual-engineering-workbench/tree/main).
The **Sync awslabs** workflow creates or fast-forwards only `awslabs` every Monday at 06:17 UTC, or manually via
**Actions → Sync awslabs → Run workflow**. Matching heads are a no-op; divergence fails for a maintainer to investigate.
It never integrates changes into Elva's `main`.

The workflow must first be reviewed and merged into `main` for scheduled and manual runs to be available.
If GitHub disables Actions or the workflow on this fork, an owner must enable it in Actions before syncing can run.
GitHub also disables scheduled workflows in public repositories after 60 days without repository activity; re-enable
the workflow if that happens. A local Git upstream remote alone does not synchronize the remote `awslabs` branch.

For individual upstream contributions, fetch `origin` and create a separate branch from `awslabs`:

```sh
git fetch origin
git switch -c contribution/my-change origin/awslabs
```

Keep each contribution focused and submit it to official upstream `main`. For Elva changes, start from Elva's `main`.

### Rebase Elva's main onto awslabs

Work in a clean, separate checkout. Fetch `origin`, record the exact current `origin/main` SHA,
and push a backup branch pointing to it before starting. Leave local deployment configuration
and unrelated uncommitted work in their existing checkout.
Use a unique backup and sync branch name for each run.

```sh
git fetch origin
git branch backup/main-before-upstream-sync origin/main
git push origin backup/main-before-upstream-sync
git switch -c sync/awslabs-main origin/main
git rebase --rebase-merges=rebase-cousins origin/awslabs
```

This retains the feature/PR merge structure while moving the feature branches onto upstream.
Git may drop a patch already present upstream. Review each conflict: retain Elva's behavior
and combine it with upstream fixes. Earlier merge resolutions can need applying again.
Compare the final tree with a separately resolved merge of the original main and awslabs
to verify that the rebase has not lost any fork changes.

Run the backend tests and both frontend checks/builds before publishing. Since a rebase rewrites
shared history, coordinate the change with maintainers and existing feature-branch owners.
Publish only after review, with a lease against the original main SHA:

```sh
git push --force-with-lease=refs/heads/main:<original-main-sha> origin HEAD:refs/heads/main
```

If the lease fails, fetch and account for the new main commits; do not override it with a plain
force push. Existing checkouts and open feature branches may need rebasing after main is updated.
Keep the backup until those branches have been reconciled.

For an upstream contribution, start from `awslabs` as above and cherry-pick only the relevant
feature commits. If a commit mixes fork-specific and upstream changes, prepare a focused new
commit on that contribution branch. A branch containing all Elva customizations is not an
upstream contribution branch.

## Contributing via Pull Requests
Contributions via pull requests are much appreciated. Before sending us a pull request, please ensure that:

1. You are working against the latest source on the *main* branch.
2. You check existing open, and recently merged, pull requests to make sure someone else hasn't addressed the problem already.
3. You open an issue to discuss any significant work - we would hate for your time to be wasted.

To send us a pull request, please:

1. Fork the repository.
2. Modify the source; please focus on the specific change you are contributing. If you also reformat all the code, it will be hard for us to focus on your change.
3. Ensure local tests pass.
4. Commit to your fork using clear commit messages.
5. Send us a pull request, answering any default questions in the pull request interface.
6. Pay attention to any automated CI failures reported in the pull request, and stay involved in the conversation.

GitHub provides additional document on [forking a repository](https://help.github.com/articles/fork-a-repo/) and
[creating a pull request](https://help.github.com/articles/creating-a-pull-request/).


## Finding contributions to work on
Looking at the existing issues is a great way to find something to contribute on. As our projects, by default, use the default GitHub issue labels (enhancement/bug/duplicate/help wanted/invalid/question/wontfix), looking at any 'help wanted' issues is a great place to start.


## Code of Conduct
This project has adopted the [Amazon Open Source Code of Conduct](https://aws.github.io/code-of-conduct).
For more information see the [Code of Conduct FAQ](https://aws.github.io/code-of-conduct-faq) or contact
opensource-codeofconduct@amazon.com with any additional questions or comments.


## Security issue notifications
If you discover a potential security issue in this project we ask that you notify AWS/Amazon Security via our [vulnerability reporting page](http://aws.amazon.com/security/vulnerability-reporting/). Please do **not** create a public github issue.


## Licensing

See the [LICENSE](LICENSE) file for our project's licensing. We will ask you to confirm the licensing of your contribution.
