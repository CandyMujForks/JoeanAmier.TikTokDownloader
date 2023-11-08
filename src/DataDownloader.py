from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from shutil import move
from types import SimpleNamespace

from emoji import replace_emoji
from requests import exceptions
from requests import get
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
)

from src.Configuration import Parameter
from src.Customizer import DESCRIPTION_LENGTH
from src.Customizer import MAX_WORKERS
from src.Customizer import (
    PROGRESS,
)
from src.DataAcquirer import retry
from src.StringCleaner import Cleaner

__all__ = ["Downloader"]


class Downloader:
    Phone_headers = {
        'User-Agent': 'com.ss.android.ugc.trill/494+Mozilla/5.0+(Linux;+Android+12;+2112123G+Build/SKQ1.211006.001;+wv)'
                      '+AppleWebKit/537.36+(KHTML,+like+Gecko)+Version/4.0+Chrome/107.0.5304.105+Mobile+Safari/537.36'}
    progress = Progress(
        TextColumn(
            "[progress.description]{task.description}",
            style=PROGRESS,
            justify="left"),
        "•",
        BarColumn(
            bar_width=20),
        "[progress.percentage]{task.percentage:>3.1f}%",
        "•",
        DownloadColumn(
            binary_units=True),
    )
    live_progress = Progress(
        TextColumn(
            "[progress.description]{task.description}",
            style=PROGRESS,
            justify="left"),
        "•",
        BarColumn(
            bar_width=20),
        "•",
        TimeElapsedColumn(),
    )

    def __init__(self, params: Parameter):
        self.cookie = params.cookie
        self.PC_headers, self.black_headers = self.init_headers(params.headers)
        self.ua_code = params.ua_code
        self.log = params.logger
        self.xb = params.xb
        self.console = params.console
        self.root = params.root
        self.folder_name = params.folder_name
        self.name_format = params.name_format
        self.split = params.split
        self.folder_mode = params.folder_mode
        self.music = params.music
        self.dynamic = params.dynamic
        self.original = params.original
        self.proxies = params.proxies
        self.download = params.download
        self.max_size = params.max_size
        self.chunk = params.chunk
        self.max_retry = params.max_retry
        self.blacklist = params.blacklist
        self.timeout = params.timeout
        self.ffmpeg = params.ffmpeg
        self.__thread = ThreadPoolExecutor
        self.__pool = None
        self.__temp = params.temp

    @staticmethod
    def init_headers(headers: dict) -> tuple:
        return headers, {"User-Agent": headers["User-Agent"]}

    def run(self,
            data: list[dict],
            type_: str,
            **kwargs, ) -> None:
        if type_ == "batch":
            self.run_batch(data, **kwargs)
        elif type_ == "works":
            self.run_general(data, **kwargs)
        else:
            raise ValueError

    def run_batch(
            self,
            data: list[dict],
            id_: str,
            name: str,
            mark="",
            addition="发布作品",
            mid: str = None,
            title: str = None,
    ):
        assert addition in {"喜欢作品", "收藏作品", "发布作品", "合集作品"}, ValueError
        mix = addition == "合集作品"
        root = self.storage_folder(
            mid or id_,
            title or name,
            not mix,
            mark,
            addition,
            mix)
        self.batch_processing(data, root)

    def run_general(self, data: list[dict], tiktok: bool):
        root = self.storage_folder()
        self.batch_processing(data, root, False, tiktok=tiktok)

    def run_live(self, data: list[tuple]):
        if not data or not self.download:
            return
        download_tasks = []
        self.generate_live_tasks(data, download_tasks)
        if self.ffmpeg:
            self.__download_live()
        else:
            self.downloader_chart(
                download_tasks,
                SimpleNamespace(),
                self.live_progress,
                len(download_tasks),
                unknown_size=True,
                headers=self.black_headers)

    def generate_live_tasks(self, data: list[tuple[dict, str]], tasks: list):
        for i, u in data:
            name = Cleaner.clean_name(
                replace_emoji(
                    f'{
                    i["title"]}{
                    self.split}{
                    i["nickname"]}' f'{
                    self.split}{
                    datetime.now().strftime("%Y-%m-%d %H.%M.%S")}.flv'))
            temp_root, actual_root = self.deal_folder_path(
                self.storage_folder(folder_name="Live"), name, True)
            tasks.append((
                u,
                temp_root,
                actual_root,
                f'直播 {i["title"]}{self.split}{i["nickname"]}',
                "0" * 19,
            ))

    def __download_live(self):
        pass

    def batch_processing(
            self,
            data: list[dict],
            root: Path,
            statistics=True,
            **kwargs):
        if not self.download:
            return
        count = SimpleNamespace(
            downloaded_image=set(),
            skipped_image=set(),
            downloaded_video=set(),
            skipped_video=set()
        )
        tasks = []
        for item in data:
            item["desc"] = item["desc"][:DESCRIPTION_LENGTH]
            name = self.generate_works_name(item)
            temp_root, actual_root = self.deal_folder_path(root, name)
            params = {
                "tasks": tasks,
                "name": name,
                "id_": item["id"],
                "item": item,
                "count": count,
                "temp_root": temp_root,
                "actual_root": actual_root
            }
            if (t := item["type"]) == "图集":
                self.download_image(**params)
            elif t == "视频":
                self.download_video(**params)
            self.download_music(**params)
            self.download_cover(**params)
        self.downloader_chart(tasks, count, self.progress, **kwargs)
        if statistics:
            self.statistics_count(count)

    def downloader_chart(
            self,
            tasks: list[tuple],
            count: SimpleNamespace,
            progress: Progress,
            max_workers=MAX_WORKERS,
            **kwargs):
        with progress:
            with self.__thread(max_workers=max_workers) as self.__pool:
                for task in tasks:
                    task_id = progress.add_task(
                        task[3], start=False, total=None)
                    # noinspection PyTypeChecker
                    self.__pool.submit(
                        self.request_file,
                        task_id,
                        *task,
                        count=count,
                        **kwargs,
                        progress=progress,
                        output=False)

    def deal_folder_path(self, root: Path, name: str,
                         pass_=False) -> tuple[Path, Path]:
        root = self.create_works_folder(root, name, pass_)
        root.mkdir(exist_ok=True)
        temp = self.__temp.joinpath(name)
        actual = root.joinpath(name)
        return temp, actual

    def is_in_blacklist(self, id_: str) -> bool:
        return id_ in self.blacklist.record

    @staticmethod
    def is_exists(path: Path) -> bool:
        return path.exists()

    def is_skip(self, id_: str, path: Path) -> bool:
        return self.is_in_blacklist(id_) or self.is_exists(path)

    def download_image(
            self,
            tasks: list,
            name: str,
            id_: str,
            item: SimpleNamespace,
            count: SimpleNamespace,
            temp_root: Path,
            actual_root: Path) -> None:
        for index, img in enumerate(
                item["downloads"].split(" "), start=1):
            if self.is_in_blacklist(id_):
                count.skipped_image.add(id_)
                self.log.info(f"图集 {id_} 存在下载记录，跳过下载")
                break
            elif self.is_exists(p := actual_root.with_name(f"{name}_{index}.jpeg")):
                self.log.info(f"图集 {id_}_{index} 文件已存在，跳过下载")
                self.log.info(f"文件路径: {p.resolve()}", False)
                continue
            tasks.append((
                img,
                temp_root.with_name(
                    f"{name}_{index}.jpeg"),
                p,
                f"图集 {id_}_{index}",
                id_,
            ))

    def download_video(
            self,
            tasks: list,
            name: str,
            id_: str,
            item: SimpleNamespace,
            count: SimpleNamespace,
            temp_root: Path,
            actual_root: Path) -> None:
        if self.is_skip(
                id_, p := actual_root.with_name(
                    f"{name}.mp4")):
            self.log.info(f"视频 {id_} 存在下载记录或文件已存在，跳过下载")
            self.log.info(f"文件路径: {p.resolve()}", False)
            count.skipped_video.add(id_)
            return
        tasks.append((
            item["downloads"],
            temp_root.with_name(f"{name}.mp4"),
            p,
            f"视频 {id_}",
            id_,
        ))

    def download_music(
            self,
            tasks: list,
            name: str,
            id_: str,
            item: SimpleNamespace,
            temp_root: Path,
            actual_root: Path,
            **kwargs, ) -> None:
        if self.check_deal_music(
                url := item["music_url"],
                p := actual_root.with_name(f"{name}.mp3"), ):
            tasks.append((
                url,
                temp_root.with_name(f"{name}.mp3"),
                p,
                f"音乐 {id_}",
                id_,
            ))

    def download_cover(
            self,
            tasks: list,
            name: str,
            id_: str,
            item: SimpleNamespace,
            temp_root: Path,
            actual_root: Path,
            **kwargs, ) -> None:
        if all((self.original,
                url := item["origin_cover"],
                not self.is_exists(p := actual_root.with_name(f"{name}.jpeg"))
                )):
            tasks.append((
                url,
                temp_root.with_name(f"{name}.jpeg"),
                p,
                f"封面 {id_}",
                id_,
            ))
        if all((self.dynamic,
                url := item["dynamic_cover"],
                not self.is_exists(p := actual_root.with_name(f"{name}.webp"))
                )):
            tasks.append((
                url,
                temp_root.with_name(f"{name}.webp"),
                p,
                f"动图 {id_}",
                id_,
            ))

    def check_deal_music(self, url: str, path: Path) -> bool:
        return all((self.music, url, not self.is_exists(path)))

    @retry
    def request_file(
            self,
            task_id,
            url: str,
            temp: Path,
            actual: Path,
            show: str,
            id_: str,
            count: SimpleNamespace,
            progress: Progress,
            headers: dict = None,
            tiktok=False,
            unknown_size=False) -> bool:
        try:
            with get(
                    url,
                    stream=True,
                    proxies=self.proxies,
                    headers=headers or self.Phone_headers if tiktok else self.PC_headers,
                    timeout=self.timeout) as response:
                if not (
                        content := int(
                            response.headers.get(
                                'content-length',
                                0))) and not unknown_size:
                    self.log.warning(f"{url} 响应内容为空", False)
                    return False
                if response.status_code != 200:
                    self.log.warning(
                        f"{response.url} 响应码异常: {response.status_code}", False)
                    return False
                elif all((self.max_size, content, content > self.max_size)):
                    self.log.info(f"{show} 文件大小超出限制，跳过下载", False)
                    return True
                return self.download_file(
                    task_id,
                    temp,
                    actual,
                    show,
                    id_,
                    response,
                    content,
                    count,
                    progress)
        except (exceptions.ConnectionError,
                exceptions.ChunkedEncodingError,
                exceptions.ReadTimeout) as e:
            self.log.warning(f"网络异常: {e}", False)
            return False

    def download_file(
            self,
            task_id,
            temp: Path,
            actual: Path,
            show: str,
            id_: str,
            response,
            content: int,
            count: SimpleNamespace,
            progress: Progress) -> bool:
        progress.update(task_id, total=content or None)
        progress.start_task(task_id)
        try:
            with temp.open("wb") as f:
                for chunk in response.iter_content(chunk_size=self.chunk):
                    f.write(chunk)
                    progress.update(task_id, advance=len(chunk))
        except exceptions.ChunkedEncodingError:
            self.log.warning(f"{show} 由于网络异常下载中断", False)
            self.delete_file(temp)
            return False
        self.save_file(temp, actual)
        self.blacklist.update_id(id_)
        self.add_count(show, id_, count)
        return True

    @staticmethod
    def add_count(type_: str, id_: str, count: SimpleNamespace):
        if type_.startswith("图集"):
            count.downloaded_image.add(id_)
        elif type_.startswith("视频"):
            count.downloaded_video.add(id_)

    def storage_folder(
            self,
            id_: str = None,
            name: str = None,
            batch=False,
            mark: str = None,
            addition: str = None,
            mix=False,
            folder_name: str = None) -> Path:
        if batch and all((id_, name, addition)):
            folder_name = f"UID{id_}_{mark or name}_{addition}"
        elif mix and all((id_, name, addition)):
            folder_name = f"MIX{id_}_{mark or name}_{addition}"
        else:
            folder_name = folder_name or self.folder_name
        folder = self.root.joinpath(folder_name)
        folder.mkdir(exist_ok=True)
        return folder

    def generate_works_name(self, data: dict) -> str:
        return Cleaner.clean_name(replace_emoji(
            self.split.join(
                data[i] for i in self.name_format)))

    def create_works_folder(
            self,
            root: Path,
            name: str,
            pass_=False) -> Path:
        if pass_:
            return root.joinpath(name)
        return root.joinpath(name) if self.folder_mode else root

    @staticmethod
    def save_file(temp: Path, actual: Path):
        move(temp.resolve(), actual.resolve())

    def delete_file(self, path: Path):
        path.unlink()
        self.log.info(f"文件 {path.name}{path.suffix} 已删除", False)

    def statistics_count(self, count: SimpleNamespace):
        self.log.info(f"跳过视频作品 {len(count.skipped_video)} 个")
        self.log.info(f"跳过图集作品 {len(count.skipped_image)} 个")
        self.log.info(f"下载视频作品 {len(count.downloaded_video)} 个")
        self.log.info(f"下载图集作品 {len(count.downloaded_image)} 个")
