"""Fabricated DOM qualification of the bounded provider citation collector."""
import unittest
from unittest.mock import AsyncMock,Mock
from evals.amazon_quick_assessment import digest
from harness.amazon_quick_sources import FIELDS,collect,validate_capture_spec
from tests.test_amazon_quick_batch import target,row

class CollectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.spec={key:('data-'+key.replace('_','-') if key.endswith('_attribute') else key) for key in FIELDS}
        self.spec.update(schema='backbond-quick-citation-ui-spec/v1',frame_url_prefix='https://pilot.example.test/embedding/',
            review_ref='SCRIPTED://observed-ui-only')
        self.target=target();self.native=row(self.target)
        self.response=Mock();self.response.get_attribute=AsyncMock(side_effect=lambda key,**kw:{
            self.spec['conversation_id_attribute']:'c-one',self.spec['user_message_id_attribute']:'m-one',
            self.spec['system_message_id_attribute']:'s-one'}[key])
        self.text=Mock();self.text.inner_text=AsyncMock(return_value='answer')
        self.button=Mock();self.button.count=AsyncMock(return_value=1);self.button.click=AsyncMock()
        self.response.locator=lambda selector:self.text if selector==self.spec['response_text_selector'] else self.button
        self.responses=Mock();self.responses.count=AsyncMock(return_value=1);self.responses.nth=lambda _:self.response
        self.item=Mock();self.item.get_attribute=AsyncMock(side_effect=lambda key,**kw:{
            self.spec['resource_id_attribute']:'document-01',self.spec['resource_type_attribute']:'document',
            self.spec['source_url_attribute']:'https://pilot.example.test/file?X-Amz-Signature=secret'}[key])
        self.snippet=Mock();self.snippet.inner_text=AsyncMock(return_value='SCRIPTED attributed instruction')
        self.item.locator=lambda _:self.snippet
        self.items=Mock();self.items.count=AsyncMock(return_value=1);self.items.nth=lambda _:self.item
        self.close=Mock();self.close.count=AsyncMock(return_value=1);self.close.click=AsyncMock()
        self.dialog=Mock();self.dialog.count=AsyncMock(side_effect=[0,1]);self.dialog.wait_for=AsyncMock()
        self.dialog.get_attribute=self.response.get_attribute
        self.dialog.locator=lambda selector:self.items if selector==self.spec['citation_selector'] else self.close
        self.frame=Mock(url=self.spec['frame_url_prefix']+'chat')
        self.frame.locator=lambda selector:self.responses if selector==self.spec['response_selector'] else self.dialog
        self.page=Mock(frames=[self.frame],main_frame=object())

    async def capture(self):return await collect(self.page,self.target,self.native,self.spec,lambda:500)

    async def test_exact_ids_and_text_bind_citation_and_query_url_is_never_exported(self):
        result=await self.capture()
        self.assertEqual(result['capture_spec_sha256'],digest(self.spec))
        self.assertIsNone(result['citations'][0]['source_url'])
        self.assertNotIn('secret',str(result));self.button.click.assert_awaited_once();self.close.click.assert_awaited_once()

    async def test_wrong_native_message_ids_never_click_and_remain_gap(self):
        self.native['system_message_id']='different'
        self.assertEqual((await self.capture())['status'],'source_evidence_unavailable')
        self.button.click.assert_not_awaited()

    async def test_reply_mismatch_or_ambiguous_frame_cannot_attribute_source(self):
        self.text.inner_text.return_value='different answer'
        self.assertEqual((await self.capture())['status'],'source_evidence_unavailable');self.button.click.assert_not_awaited()
        self.page.frames.append(self.frame)
        self.assertEqual((await self.capture())['status'],'source_evidence_unavailable')

    async def test_unconfigured_collector_does_nothing(self):
        self.assertIsNone(await collect(None,self.target,self.native,None,lambda:500))

    async def test_preexisting_stale_dialog_is_rejected_before_click(self):
        self.dialog.count=AsyncMock(return_value=1)
        self.assertEqual((await self.capture())['status'],'source_evidence_unavailable')
        self.button.click.assert_not_awaited()

    async def test_fresh_dialog_with_wrong_response_binding_remains_gap(self):
        self.dialog.get_attribute=AsyncMock(return_value='stale-message')
        self.assertEqual((await self.capture())['status'],'source_evidence_unavailable')
        self.close.click.assert_awaited_once()

if __name__=='__main__':unittest.main()
