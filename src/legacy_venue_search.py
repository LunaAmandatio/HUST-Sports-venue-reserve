from hust_reserve import HustReserve


class VenueSearch(HustReserve):
    MODE_NAME = '即时场地查询'

    # 扫描用户选择的时段（或全天），在结果窗口中手动决定是否预约。
    def run_selection(self, name, visit_date, slots):
        results, unconfirmed = self.scan_target(name, visit_date, slots)
        self.scenes(name, visit_date, results, unconfirmed)
