-- Cross-artist reserve records for the preference-driven Song World Cup.
-- Metadata is seeded from the public Apple Music/iTunes catalogue; only artwork URLs are stored.
SET @artist_id = UUID_TO_BIN('c4f4ea31-0a35-4f70-a8f2-3d093d1d1707');

INSERT INTO artist (id, name, sort_name) VALUES (@artist_id, '陈绮贞', 'cheer chen')
ON DUPLICATE KEY UPDATE name = VALUES(name);

INSERT INTO recording (id, artist_id, title, album_title, seed_rank, cover_url, cover_source, cover_status, cover_fetched_at) VALUES
(UUID_TO_BIN('20000000-0000-4000-8000-000000000001'), @artist_id, '让我想一想', '让我想一想', 1, 'https://is1-ssl.mzstatic.com/image/thumb/Music115/v4/f6/9a/b6/f69ab634-a5cc-8e1f-9548-3185f42549aa/dj.mgpysrjj.jpg/600x600bb.jpg', 'APPLE_ITUNES', 'AVAILABLE', CURRENT_TIMESTAMP(3)),
(UUID_TO_BIN('20000000-0000-4000-8000-000000000002'), @artist_id, '还是会寂寞', '还是会寂寞', 2, 'https://is1-ssl.mzstatic.com/image/thumb/Music19/v4/e0/0e/ae/e00eaefb-5737-4833-69c9-93be29dc1c6d/mzm.nshgqvxy.jpg/600x600bb.jpg', 'APPLE_ITUNES', 'AVAILABLE', CURRENT_TIMESTAMP(3)),
(UUID_TO_BIN('20000000-0000-4000-8000-000000000003'), @artist_id, '太聪明', '还是会寂寞', 3, 'https://is1-ssl.mzstatic.com/image/thumb/Music19/v4/e0/0e/ae/e00eaefb-5737-4833-69c9-93be29dc1c6d/mzm.nshgqvxy.jpg/600x600bb.jpg', 'APPLE_ITUNES', 'AVAILABLE', CURRENT_TIMESTAMP(3)),
(UUID_TO_BIN('20000000-0000-4000-8000-000000000004'), @artist_id, '旅行的意义', '旅行的意义', 4, 'https://is1-ssl.mzstatic.com/image/thumb/Music124/v4/d3/fc/2c/d3fc2c39-edf1-f462-f4e4-39fade8a444e/4713108171331.jpg/600x600bb.jpg', 'APPLE_ITUNES', 'AVAILABLE', CURRENT_TIMESTAMP(3)),
(UUID_TO_BIN('20000000-0000-4000-8000-000000000005'), @artist_id, '华丽的冒险', '华丽的冒险', 5, 'https://is1-ssl.mzstatic.com/image/thumb/Music/v4/c5/c5/b9/c5c5b9a5-7b08-1579-950e-6ad69d8f105a/2005_9-_1400.jpg/600x600bb.jpg', 'APPLE_ITUNES', 'AVAILABLE', CURRENT_TIMESTAMP(3)),
(UUID_TO_BIN('20000000-0000-4000-8000-000000000006'), @artist_id, '花的姿态', '华丽的冒险', 6, 'https://is1-ssl.mzstatic.com/image/thumb/Music/v4/c5/c5/b9/c5c5b9a5-7b08-1579-950e-6ad69d8f105a/2005_9-_1400.jpg/600x600bb.jpg', 'APPLE_ITUNES', 'AVAILABLE', CURRENT_TIMESTAMP(3)),
(UUID_TO_BIN('20000000-0000-4000-8000-000000000007'), @artist_id, '太阳', '太阳', 7, 'https://is1-ssl.mzstatic.com/image/thumb/Music211/v4/07/70/06/077006a5-18c5-0e56-0e0c-ba052672b07a/4719760090010_new.jpg/600x600bb.jpg', 'APPLE_ITUNES', 'AVAILABLE', CURRENT_TIMESTAMP(3)),
(UUID_TO_BIN('20000000-0000-4000-8000-000000000008'), @artist_id, '鱼', '太阳', 8, 'https://is1-ssl.mzstatic.com/image/thumb/Music211/v4/07/70/06/077006a5-18c5-0e56-0e0c-ba052672b07a/4719760090010_new.jpg/600x600bb.jpg', 'APPLE_ITUNES', 'AVAILABLE', CURRENT_TIMESTAMP(3));
