from dataclasses import dataclass

from ..models import Vote
from ..voting import voting_progress


class VotingClosed(Exception):
    pass


class SelfVoteNotAllowed(Exception):
    pass


class SubmissionRoundMismatch(Exception):
    pass


class InvalidVoteScore(Exception):
    pass


@dataclass(frozen=True)
class VoteMutationResult:
    vote: Vote
    created: bool
    progress: dict


@dataclass(frozen=True)
class VoteCommand:
    round_obj: object
    voter: object
    submission: object

    def save(self, score):
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 5:
            raise InvalidVoteScore

        vote, created = Vote.objects.update_or_create(
            round=self.round_obj,
            voter=self.voter,
            submission=self.submission,
            defaults={'score': score},
        )
        return VoteMutationResult(
            vote=vote,
            created=created,
            progress=voting_progress(self.round_obj, self.voter),
        )


def prepare_vote(round_obj, voter, submission):
    """Validate a vote target and return its single-rating mutation command."""
    if round_obj.state != 'voting':
        raise VotingClosed
    if submission.round_id != round_obj.id:
        raise SubmissionRoundMismatch
    if submission.user_id == voter.id:
        raise SelfVoteNotAllowed
    return VoteCommand(round_obj, voter, submission)


def record_vote(round_obj, voter, submission, score):
    """Validate and persist one rating, returning post-save ballot progress."""
    return prepare_vote(round_obj, voter, submission).save(score)
