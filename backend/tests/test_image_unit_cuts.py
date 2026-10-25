import copy
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import image_unit as I


class CutRuleTests(unittest.TestCase):
    def setUp(self):
        self.row=dict(segment_id='line',start=0.,end=10.,text='Invented words.',words=[dict(start=0.,end=2.),dict(start=2.4,end=10.)])

    def test_sorted_cuts_nearest_and_after_bounds(self):
        self.assertEqual(I.cuts_between([3,2,2,float('nan'),-1,10],1,4),[2.,3.])
        self.assertEqual(I.snap_to_cut(2.5,[3,2],.5),2.)
        self.assertIsNone(I.snap_to_cut(2.5,[3,2],.49))
        self.assertEqual(I.next_cut_after({'end':2},[2,3,4],1.5),3.)
        self.assertIsNone(I.next_cut_after({'end':2},[4],1.5))

    def test_only_long_lines_with_nearby_timed_gaps_generate_suggestions(self):
        saved=copy.deepcopy(self.row)
        out=I.plan_cut_splits([self.row],[2.3])
        self.assertEqual(len(out),1);self.assertEqual(out[0]['word_index'],1)
        self.assertEqual(out[0]['reason'],'shot_cut_near_timed_word_gap')
        self.assertEqual(self.row,saved)
        short=dict(self.row,end=5.,words=[dict(start=0.,end=2.),dict(start=2.4,end=5.)])
        self.assertEqual(I.plan_cut_splits([short],[2.3]),[])
        tiny=copy.deepcopy(self.row);tiny['words'][1]['start']=2.1
        self.assertEqual(I.plan_cut_splits([tiny],[2.05]),[])
        self.assertEqual(I.plan_cut_splits([self.row],[4.5]),[])
        self.assertEqual(I.plan_cut_splits([dict(self.row,words=[])],[2.3]),[])

    def test_missing_corrupt_audio_files_and_ffmpeg_errors_are_free(self):
        self.assertEqual(I.detect_cuts(None),[])
        self.assertEqual(I.detect_cuts('does-not-exist'),[])
        with patch.object(I,'video_info',return_value={'duration':8.}):
            with patch.object(I.subprocess,'run',side_effect=OSError('missing ffmpeg')):
                self.assertEqual(I.detect_cuts('invented.mp4'),[])

    def test_audio_with_album_art_is_not_video_and_real_stream_skips_attached_cover(self):
        with tempfile.TemporaryDirectory() as folder:
            audio=Path(folder)/'cover.mp3';audio.write_bytes(b'invented')
            with patch.object(I.subprocess,'run',side_effect=AssertionError('audio must not be probed for pictures')):
                self.assertIsNone(I.video_info(audio))
                self.assertEqual(I.detect_cuts(audio),[])
            video=Path(folder)/'cover-and-video.mp4';video.write_bytes(b'invented')
            art=dict(index=0,width=200,height=200,disposition={'attached_pic':1})
            data=dict(streams=[art],format={'duration':'60'})
            fake=type('Probe',(),{})();fake.stdout=json.dumps(data).encode()
            with patch.object(I.subprocess,'run',return_value=fake):
                self.assertIsNone(I.video_info(video))
                data['streams'].append(dict(index=3,width=640,height=360,disposition={'attached_pic':0}))
                fake.stdout=json.dumps(data).encode()
                self.assertEqual(I.video_info(video)['index'],3)


def synthetic(path):
    command=['ffmpeg','-hide_banner','-loglevel','error','-nostdin','-y']
    for colour,length in [('red',2),('white',2),('blue',4)]:
        command+=['-f','lavfi','-i',f'color={colour}:s=320x180:r=10:d={length}']
    command+=['-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=8',
              '-filter_complex','[2:v]fade=t=out:st=2:d=2[b];[0:v][1:v][b]concat=n=3:v=1:a=0[v]',
              '-map','[v]','-map','3:a:0','-c:v','libx264','-preset','ultrafast','-crf','30','-pix_fmt','yuv420p',
              '-c:a','aac','-threads','1',str(path)]
    subprocess.run(command,check=True,capture_output=True,timeout=45)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'ffmpeg/ffprobe are not on PATH; synthetic video checks skipped')
class SyntheticCutsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.video=Path(cls.temp.name)/'blocks.mp4';synthetic(cls.video)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_hard_cuts_at_two_and_four_seconds_slow_fade_not_storm(self):
        cuts=I.detect_cuts(self.video)
        self.assertEqual(len(cuts),2,cuts)
        self.assertAlmostEqual(cuts[0],2.,delta=.21);self.assertAlmostEqual(cuts[1],4.,delta=.21)

    def test_silent_clip_has_no_audio_and_bounded_dimensions_and_fps(self):
        target=Path(self.temp.name)/'small.mp4'
        self.assertEqual(I.prepare_clip(self.video,target,0,8,{'long_side':240,'fps':2}),target)
        data=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(target)]))
        self.assertEqual([s['codec_type'] for s in data['streams']],['video'])
        s=data['streams'][0];self.assertLessEqual(max(s['width'],s['height']),240)
        self.assertEqual(s['r_frame_rate'],'2/1');self.assertEqual(s['codec_name'],'h264')
        self.assertEqual(I.prepare_clip(self.video,target,0,8,{'long_side':9999}),None)


if __name__=='__main__':unittest.main()
