import unittest
from unittest.mock import MagicMock, patch
from queue import PriorityQueue
from voice.voice import VoiceInput, VoiceOutput
from agent.agent import LLM

class TestVoiceSystem(unittest.TestCase):
    def test_voice_input_priority(self):
        vi = VoiceInput()
        vi.queue = PriorityQueue()
        self.assertEqual(vi.get_task_priority("please stop"), 5)
        self.assertEqual(vi.get_task_priority("record now"), 2)
        self.assertEqual(vi.get_task_priority("pause it"), 3)
        self.assertEqual(vi.get_task_priority("do something"), 1)

    @patch('pyttsx3.init')
    def test_voice_output_queue(self, mock_init):
        engine_mock = MagicMock()
        mock_init.return_value = engine_mock

        vo = VoiceOutput()
        vo.add_to_queue("Hello world", priority=2)
        self.assertFalse(vo.queue.empty())
        prio, msg = vo.queue.get()
        self.assertEqual(msg, "Hello world")
        self.assertEqual(prio, 2)

    def test_llm_decorator(self):
        llm = LLM(no_workers=1)
        fake_func = MagicMock()

        @llm.llm_wrapper
        def wrapped_func(msg, priority):
            fake_func(msg, priority)

        # Simulate input
        wrapped_func("test message", 2)
        self.assertFalse(llm.llm_task_queue.empty())

        # Manually process for testing
        priority, (msg, func) = llm.llm_task_queue.get()
        self.assertEqual(msg, "test message")
        func("mock response", priority)
        fake_func.assert_called_with("mock response", 2)

if __name__ == '__main__':
    unittest.main()
