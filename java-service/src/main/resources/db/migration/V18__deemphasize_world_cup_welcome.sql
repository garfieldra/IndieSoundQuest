UPDATE conversation_message
SET text_content = '最近想从哪种声音开始？可以告诉我喜欢的艺人、反复听的歌、某种情绪或一个聆听场景。'
WHERE role = 'SYSTEM'
  AND type = 'SYSTEM_NOTE'
  AND text_content = '我们的歌曲世界杯应该从哪开始？描述一下你的音乐喜好，或告诉我最近反复听的歌。';
