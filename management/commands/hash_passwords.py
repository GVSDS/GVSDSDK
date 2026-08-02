"""将 User 表中明文密码迁移为 bcrypt 哈希"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = '将 User 表中所有明文密码迁移为 bcrypt 哈希'

    def handle(self, *args, **options):
        # 筛选非空且非 bcrypt 哈希的密码
        users = User.objects.exclude(UserPassword='')
        updated = 0
        skipped = 0

        for user in users.iterator():
            stored = user.UserPassword or ''
            # 已是 bcrypt 哈希则跳过
            if stored.startswith(('$2b$', '$2a$')) and len(stored) == 60:
                skipped += 1
                continue
            # 明文密码 → bcrypt 哈希
            user.SetPassword(stored)
            user.save(update_fields=['UserPassword'])
            updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'完成：{updated} 条明文密码已转为 bcrypt，{skipped} 条已是哈希跳过'
        ))
