"""Versioned portable case archive contract shared with the gateway.

No networking, application globals or third-party dependencies. Keep the copy in
sam_annotation_web_tool byte-identical when changing the transfer contract.
"""
from hashlib import sha256
import json
import math
from pathlib import PurePosixPath
import re
import stat
import zipfile

SCHEMA = 'sam-annotation.case-bundle.v1'
MAX_ARCHIVE = 2 * 1024**3
MAX_UNPACKED = 8 * 1024**3
MAX_ENTRIES = 50000
MAX_MANIFEST = 16 * 1024**2


class BundleError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest_file(path):
    result = sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024**2), b''):
            result.update(chunk)
    return result.hexdigest()


def seal(manifest):
    manifest = {key: value for key, value in manifest.items() if key != 'bundle_id'}
    return {**manifest, 'bundle_id': sha256(canonical(manifest)).hexdigest()}


def valid_number(value, minimum=0, maximum=7*86400):
    return type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum


def jpeg_dimensions(raw):
    if not raw.startswith(b'\xff\xd8') or not raw.endswith(b'\xff\xd9'):
        raise BundleError('JPEG 文件头尾无效')
    offset=2
    while offset+4<=len(raw):
        if raw[offset]!=255:
            raise BundleError('JPEG 标记无效')
        while offset<len(raw) and raw[offset]==255:offset+=1
        if offset>=len(raw):break
        marker=raw[offset];offset+=1
        if marker in {0xd8,0xd9,0x01} or 0xd0<=marker<=0xd7:continue
        length=int.from_bytes(raw[offset:offset+2],'big')
        if length<2 or offset+length>len(raw):break
        if marker in {0xc0,0xc1,0xc2} and length>=8:
            height=int.from_bytes(raw[offset+3:offset+5],'big')
            width=int.from_bytes(raw[offset+5:offset+7],'big')
            if width and height:return width,height
            break
        if marker==0xda:break
        offset+=length
    raise BundleError('JPEG 缺少有效尺寸信息')


def inspect_archive(path, destination):
    """Validate all identities before returning extracted files under destination.

    Caller supplies a fresh temporary directory. Never use extractall. Limits are
    applied to both declared and streamed bytes, and only listed files are read.
    """
    try:
        return _inspect(path, destination)
    except (KeyError, TypeError, ValueError, OverflowError, RecursionError, OSError, zipfile.BadZipFile, RuntimeError) as error:
        if isinstance(error, BundleError):
            raise
        raise BundleError('病例包格式无效或文件损坏') from error


def _inspect(path, destination):
    if path.stat().st_size > MAX_ARCHIVE:
        raise BundleError('病例包超过 2 GiB')
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]
        if not entries or len(entries) > MAX_ENTRIES or len(set(names)) != len(names) or 'manifest.json' not in names:
            raise BundleError('病例包清单缺失、重复或文件数量超限')
        total = 0
        for item in entries:
            parts = PurePosixPath(item.filename).parts
            if (not parts or item.filename.startswith('/') or '\\' in item.filename or ':' in item.filename
                or any(part in {'.', '..'} for part in parts) or str(PurePosixPath(item.filename)) != item.filename
                or item.is_dir() or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1):
                raise BundleError('病例包包含不安全的路径或特殊文件')
            total += item.file_size
            if total > MAX_UNPACKED or item.file_size > 64*1024**2 or item.file_size > max(1024**2, item.compress_size*300):
                raise BundleError('病例包解压大小或压缩比超限')
        if archive.getinfo('manifest.json').file_size > MAX_MANIFEST:
            raise BundleError('病例包清单过大')
        manifest = json.loads(archive.read('manifest.json'))
        if not isinstance(manifest, dict) or manifest.get('schema') != SCHEMA or seal(manifest)['bundle_id'] != manifest.get('bundle_id'):
            raise BundleError('病例包版本或清单摘要无效')
        cases, files = manifest.get('cases'), manifest.get('files')
        if not isinstance(cases, list) or not 1 <= len(cases) <= 100 or not isinstance(files, list):
            raise BundleError('病例包须包含 1–100 个病例')
        indexed = {}
        for item in files:
            name = item['path']
            if (not isinstance(name, str) or name in indexed or not name.startswith('cases/')
                or name == 'manifest.json' or not re.fullmatch('[a-f0-9]{64}', item['sha256'])
                or type(item['size_bytes']) is not int or item['size_bytes'] < 1):
                raise BundleError('文件清单无效')
            indexed[name] = item
        if set(indexed) | {'manifest.json'} != set(names):
            raise BundleError('病例包包含未登记或缺失文件')
        used, ids = set(), set()
        for case in cases:
            identifier = case['portable_id']
            if not re.fullmatch('[a-f0-9]{64}', identifier) or identifier in ids:
                raise BundleError('病例标识无效或重复')
            ids.add(identifier)
            if (not isinstance(case.get('case_id'),str) or not 1<=len(case['case_id'])<=160
                or type(case.get('annotation_revision')) is not int or case['annotation_revision']<0
                or not isinstance(case.get('profile'),dict) or not isinstance(case['profile'].get('details'),dict)
                or any(not isinstance(v,str) or len(v)>10000 for v in case['profile']['details'].values())):
                raise BundleError('病例信息或版本格式无效')
            video, extraction = case['video'], case['extraction']
            if (not re.fullmatch('[a-f0-9]{64}', video['sha256'])
                or not valid_number(video['duration_seconds'], .001)
                or any(type(video[key]) is not int or not 1 <= video[key] <= 16384 for key in ('width','height'))
                or type(video.get('size_bytes')) is not int or video['size_bytes'] <= 0
                or not isinstance(video.get('original_filename'), str) or len(video['original_filename']) > 240
                or not valid_number(extraction['fps'], .1, 30)
                or extraction['revision'] != case['annotation_revision'] or case['review_state'] != 'reviewed'):
                raise BundleError('视频来源或抽帧复核版本无效')
            if not isinstance(case['rows'], dict) or set(case['rows']) != {'case','phase','finding'}:
                raise BundleError('病例标注结构无效')
            for table, rows in case['rows'].items():
                if not isinstance(rows, list) or len(rows)>10000:
                    raise BundleError('标注记录数量超限')
                for row in rows:
                    if not isinstance(row, dict) or any(not isinstance(v, str) or len(v)>10000 for v in row.values()):
                        raise BundleError('标注字段无效')
                    if row.get('case_id') != case['case_id']:
                        raise BundleError('标注病例标识不匹配')
                    if table != 'case' and (not valid_number(float(row['start_sec'])) or not valid_number(float(row['end_sec'])) or not 0<=float(row['start_sec'])<float(row['end_sec'])<=video['duration_seconds']):
                        raise BundleError('标注时间超出视频范围')
            phases = sorted(case['rows']['phase'],key=lambda row:float(row['start_sec']))
            if any(row.get('phase_label') not in {'unused','effective_observation'} or row.get('visible_side') not in {'left','right','unknown'}
                   or row.get('phase_label')=='effective_observation' and row.get('quality_level') not in {'1','2'} for row in phases):
                raise BundleError('阶段分类、画质或侧别无效')
            if len({row['segment_id'] for row in phases})!=len(phases) or any(float(a['end_sec'])>float(b['start_sec']) for a,b in zip(phases,phases[1:])):
                raise BundleError('阶段存在重叠或重复编号')
            readable = {row['segment_id']: row for row in phases if row.get('phase_label')=='effective_observation' and row.get('quality_level')=='2'}
            frames=case['frames']
            if not isinstance(frames,list) or not 1<=len(frames)<=20000:
                raise BundleError('病例须包含 1–20000 张帧')
            previous=-1
            for frame in frames:
                name=frame['path']
                if (name not in indexed or name in used or str(PurePosixPath(name).parent)!='cases/'+identifier+'/frames' or not name.endswith('.jpg')
                    or frame['sha256']!=indexed[name]['sha256'] or frame['size_bytes']!=indexed[name]['size_bytes']
                    or not re.fullmatch('[a-f0-9]{32}',frame['md5'])
                    or not valid_number(frame['timestamp_seconds']) or frame['timestamp_seconds']<=previous
                    or frame['width']!=video['width'] or frame['height']!=video['height']):
                    raise BundleError('帧身份、时间或尺寸无效')
                phase=readable.get(frame['segment_id'])
                if not phase or not float(phase['start_sec'])<=frame['timestamp_seconds']<float(phase['end_sec']):
                    raise BundleError('帧不在可判读有效阶段内')
                previous=frame['timestamp_seconds'];used.add(name)
            for key in ('annotation_path','report_path'):
                name=case[key]
                if name not in indexed or not name.startswith('cases/'+identifier+'/') or name in used:
                    raise BundleError('病例报告路径无效')
                used.add(name)
        if used!=set(indexed):
            raise BundleError('存在未关联到病例的文件')
        for name, item in indexed.items():
            info=archive.getinfo(name)
            if info.file_size!=item['size_bytes']:
                raise BundleError('文件大小与清单不一致')
            target=destination/name
            target.parent.mkdir(parents=True,exist_ok=True)
            digest,count=sha256(),0
            with archive.open(info) as source,target.open('xb') as output:
                for chunk in iter(lambda:source.read(1024**2),b''):
                    count+=len(chunk)
                    if count>item['size_bytes']:
                        raise BundleError('实际文件大小超限')
                    digest.update(chunk);output.write(chunk)
            if count!=item['size_bytes'] or digest.hexdigest()!=item['sha256']:
                raise BundleError('文件摘要校验失败')
        for case in cases:
            annotation=json.loads((destination/case['annotation_path']).read_bytes())
            report=json.loads((destination/case['report_path']).read_bytes())
            if annotation!={'rows':case['rows'],'profile':case['profile']} or not isinstance(report,dict) or report.get('case_id')!=case['case_id'] or report.get('phases')!=case['rows']['phase'] or report.get('findings')!=case['rows']['finding']:
                raise BundleError('独立报告或标注文件与清单不一致')
        return manifest
