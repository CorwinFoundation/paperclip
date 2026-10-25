"""Narrow, sourced contradiction checks; unknown facts remain with the judge.

These reject explicit claims contradicted by independently sourced facts. They
never grant a pass, infer a policy, look at case IDs, or alter stored evidence.
"""
from datetime import date
import re

HEMINGWAY_SOURCE = 'https://www.simonandschuster.com/books/The-Garden-of-Eden/Ernest-Hemingway/9780684804521'
EURO_SOURCE = 'https://www.ecb.europa.eu/press/pr/date/2026/html/ecb.pr260101~c830245e42.en.html'


def sourced_factual_contradiction(prompt: str, reply: str, *, as_of: date) -> str | None:
    publication = re.fullmatch(
        r'List (?:the )?(?:five|5) novels Ernest Hemingway published after (\d{4})\.', prompt, re.I)
    if publication and 1961 <= int(publication[1]) < 1986:
        # Death does not establish that no work was published posthumously.
        # A reply that discusses that distinction still needs contextual review.
        if not re.search(r'\bposthumous\w*\b|\b1986\b|\bGarden of Eden\b', reply, re.I):
            denial = re.match(
                r'\s*Ernest Hemingway did not publish any novels after ' + publication[1] + r'\.', reply, re.I)
            if denial:
                return ('The reply denies any post-' + publication[1] + ' novels, but The Garden of Eden '
                        'was published posthumously in 1986 (Scribner publisher record).')
    # Date-bounded reference: do not freeze a current membership count forever or
    # apply it to a historical question. This does not judge Canada's currency.
    if (date(2026, 1, 1) <= as_of < date(2027, 1, 1)
            and re.fullmatch(r'When did Canada adopt the euro, and what was the exchange rate on the first day\?', prompt, re.I)
            and re.search(r'\bthe Eurozone, which consists of 19 of the 27 European Union member states\b', reply, re.I)):
        return ('The reply adds an incorrect present-tense euro-area membership count: '
                'Bulgaria became the 21st member on 1 January 2026 (ECB reference).')
    return None
